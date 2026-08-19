"""Violation injection and the pre-registered ground-truth catalogue.

Phase 4 deliverable. Injection operates at SPAN level (PLAN-REVIEW B1): each
registered fault function is a pure, seeded transformation of one session's
span JSONL rows, after which the faulted spans are re-mapped through the
Phase 3 mapper — so every downstream system consumes artifacts derived from
the same faulted telemetry, and evidence records cite span ids that actually
exhibit the fault.

Surgery discipline: attributes are only ever rewritten through
``parse_event -> dataclasses.replace -> validate_event -> flatten_event``;
``at.obj.N`` indices and counts re-canonicalise automatically and are never
hand-edited. Mutated spans keep their identity (span_id, trace_id,
creation_index, wall nanos); deleted spans vanish; ``at.event.seq`` is
renumbered densely once per session after all faults; event ids stay stable
(their numeric infix is the PRE-injection seq — documented here and in the
labels file so nobody "fixes" it later); ``session_end.event_count`` is
recomputed so a deletion looks native rather than leaving a detectable
tell.

Three faults are deliberately NOT the naive mutation, because the strict
reader/mapper would reject it (each trap is pinned in tests):

- V3 injects an out-of-roster approver declared only in the faulted session;
  mutating a shared ``APR-*`` object's declared role is a cross-trace
  redeclaration conflict.
- V5 renames the demographics resource to a session-local id with an empty
  ``lawful_basis``; blanking the shared ``RES-DEMOGRAPHICS`` is the same
  conflict.
- V6 blanks the ``CreditDecision`` OBJECT's ``reason_codes`` (T4 reads the
  object attribute); ``validate_event`` rejects an adverse event whose
  EVENT attribute is empty, and flipping ``adverse`` would silently change
  semantics.

Import policy (hard): stdlib + pyyaml + ``model/span_contract`` +
``model/ocel_schema`` only. ``eval/metrics.py`` imports
:class:`GroundTruthViolation` from here, so this module must stay
importable without the ``scenario`` extra (no telemetry/otel imports).

Invariant I4: ``data/catalogue/violations.yaml`` is frozen, git-tagged,
pushed, and externally timestamped before any detection experiment runs.
Never edit it after the tag. If it is wrong, stop and report.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Literal

import yaml

from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import (
    EVENT_SEQ,
    EventSpec,
    flatten_event,
    parse_event,
    parse_scope,
    validate_event,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

Split = Literal["clean", "single", "mixed"]
SPLITS: Final[tuple[Split, ...]] = ("clean", "single", "mixed")

SpanRow = dict[str, Any]
SessionRows = tuple[SpanRow, ...]


class InjectionError(ValueError):
    """The injection request cannot be satisfied. Raised, never repaired."""


class InjectionInputMissingError(InjectionError):
    """The caller-named base run (or a manifest-listed file) does not exist.

    A distinct type so the CLI maps it to "input unavailable" (exit 3)
    without sniffing message text — free-text error messages can contain
    any substring.
    """


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _rank(*parts: object) -> str:
    """The house deterministic ranking key (cf. scenario/seed.py)."""
    return hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

_ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "name",
        "description",
        "injection_function",
        "detected_by",
        "articles",
        "severity",
        "perturbation_axes",
        "co_injectable_with",
        "evidence_anchor",
    }
)
_SEVERITIES: Final[frozenset[str]] = frozenset({"high", "medium", "low"})
#: A legal article reference must actually name an article ("Art. <n>...");
#: hard rule 4 is about substance, not just non-emptiness — "TBD" fails.
_ARTICLE_PATTERN: Final = r"Art\.\s*\d"
_CONSTRAINT_ID_PATTERN: Final = r"^[A-Z][A-Z0-9]*\.[a-z][a-z0-9_]*$"
_TOP_KEYS: Final[frozenset[str]] = frozenset(
    {"schema_version", "catalogue_version", "scenario_name", "scenario_version", "violations"}
)


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """One pre-registered violation class."""

    entry_id: str
    name: str
    description: str
    injection_function: str
    constraint_id: str
    articles: tuple[str, ...]
    severity: str
    perturbation_axes: tuple[str, ...]
    co_injectable_with: tuple[str, ...]
    evidence_anchor: str


@dataclass(frozen=True, slots=True)
class Catalogue:
    """The loaded, validated violation catalogue."""

    schema_version: int
    catalogue_version: str
    scenario_name: str
    scenario_version: str
    entries: tuple[CatalogueEntry, ...]
    sha256: str


def catalogue_hash(path: Path) -> str:
    """Return the catalogue's sha256 over its exact file bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_catalogue(path: Path) -> Catalogue:
    """Load the catalogue. Raises if any entry lacks an article reference."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name}: catalogue is not a mapping")
    if doc.get("schema_version") != 1:
        raise ValueError(f"{path.name}: schema_version {doc.get('schema_version')!r} is not 1")
    unknown_top = set(doc) - _TOP_KEYS
    if unknown_top:
        raise ValueError(f"{path.name}: unknown top-level key(s) {sorted(unknown_top)}")
    raw_entries = doc.get("violations")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError(f"{path.name}: 'violations' must be a non-empty list")

    entries: list[CatalogueEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: violation entry is not a mapping")
        unknown = set(raw) - _ENTRY_KEYS
        if unknown:
            raise ValueError(
                f"{path.name}: entry {raw.get('id', '?')} has unknown key(s) "
                f"{sorted(unknown)} (e.g. {sorted(unknown)[0]})"
            )
        missing = _ENTRY_KEYS - set(raw)
        if missing:
            raise ValueError(
                f"{path.name}: entry {raw.get('id', '?')} missing key(s) {sorted(missing)}"
            )
        import re as _re

        articles = tuple(str(a) for a in raw["articles"])
        if not articles or any(not _re.search(_ARTICLE_PATTERN, a) for a in articles):
            raise ValueError(
                f"{path.name}: entry {raw['id']} lacks a substantive legal article "
                f"reference (need 'Art. <number>'); hard rule 4 — rulesets without "
                "one fail validation by design"
            )
        severity = str(raw["severity"])
        if severity not in _SEVERITIES:
            raise ValueError(
                f"{path.name}: entry {raw['id']} severity {severity!r} is not one "
                f"of {sorted(_SEVERITIES)}"
            )
        constraint_id = str(raw["detected_by"])
        if not _re.match(_CONSTRAINT_ID_PATTERN, constraint_id):
            raise ValueError(
                f"{path.name}: entry {raw['id']} detected_by {constraint_id!r} does "
                "not look like a constraint id (e.g. T1.synchronised_approval)"
            )
        function_name = str(raw["injection_function"])
        if function_name not in REGISTRY:
            raise ValueError(
                f"{path.name}: entry {raw['id']} names injection function "
                f"{function_name!r}, which is not registered"
            )
        entries.append(
            CatalogueEntry(
                entry_id=str(raw["id"]),
                name=str(raw["name"]),
                description=str(raw["description"]),
                injection_function=function_name,
                constraint_id=constraint_id,
                articles=articles,
                severity=severity,
                perturbation_axes=tuple(str(a) for a in raw["perturbation_axes"]),
                co_injectable_with=tuple(str(v) for v in raw["co_injectable_with"]),
                evidence_anchor=str(raw["evidence_anchor"]),
            )
        )

    ids = [e.entry_id for e in entries]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"{path.name}: duplicate entry id(s) {sorted(duplicates)}")
    by_id = {e.entry_id: e for e in entries}
    for entry in entries:
        for other_id in entry.co_injectable_with:
            if other_id not in by_id:
                raise ValueError(f"{path.name}: {entry.entry_id} lists unknown class {other_id!r}")
            if entry.entry_id not in by_id[other_id].co_injectable_with:
                raise ValueError(
                    f"{path.name}: co_injectable_with is not symmetric: "
                    f"{entry.entry_id} lists {other_id} but not vice versa"
                )

    return Catalogue(
        schema_version=int(doc["schema_version"]),
        catalogue_version=str(doc["catalogue_version"]),
        scenario_name=str(doc["scenario_name"]),
        scenario_version=str(doc["scenario_version"]),
        entries=tuple(entries),
        sha256=catalogue_hash(path),
    )


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroundTruthViolation:
    """One injected fault: its id, the events it touched, and the constraint it breaks."""

    violation_id: str
    fault_class: str
    constraint_id: str
    session_id: str
    ocel_event_ids: tuple[str, ...]
    ocel_object_ids: tuple[str, ...]
    expected_evidence_span_ids: tuple[str, ...]
    removed_span_ids: tuple[str, ...]
    severity: str
    articles: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class ViolationDraft:
    """A :class:`GroundTruthViolation` before its content-addressed id exists."""

    fault_class: str
    constraint_id: str
    session_id: str
    ocel_event_ids: tuple[str, ...]
    ocel_object_ids: tuple[str, ...]
    expected_evidence_span_ids: tuple[str, ...]
    removed_span_ids: tuple[str, ...]
    severity: str
    articles: tuple[str, ...]
    description: str


def _finalise(
    draft: ViolationDraft, base_run_id: str, seed: int, catalogue_sha256: str
) -> GroundTruthViolation:
    """Content-address the violation id — deliberately WITHOUT the log hash,
    so the injector stays a pure single-pass span function and a mapper
    bugfix can never churn frozen label ids (amendment note in BUILD-PLAN
    Phase 4)."""
    payload = {
        "base_run_id": base_run_id,
        "catalogue_sha256": catalogue_sha256,
        "constraint_id": draft.constraint_id,
        "fault_class": draft.fault_class,
        "injector_seed": seed,
        "ocel_event_ids": sorted(draft.ocel_event_ids),
        "removed_span_ids": sorted(draft.removed_span_ids),
        "session_id": draft.session_id,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:16]
    return GroundTruthViolation(
        violation_id=digest,
        fault_class=draft.fault_class,
        constraint_id=draft.constraint_id,
        session_id=draft.session_id,
        ocel_event_ids=draft.ocel_event_ids,
        ocel_object_ids=draft.ocel_object_ids,
        expected_evidence_span_ids=draft.expected_evidence_span_ids,
        removed_span_ids=draft.removed_span_ids,
        severity=draft.severity,
        articles=draft.articles,
        description=draft.description,
    )


# ---------------------------------------------------------------------------
# Session views
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionView:
    """Read-only pre-pass summary of one session, driving eligibility."""

    session_id: str
    outcome: str
    decision_id: str
    has_grant_approval: bool
    approver_id: str | None
    handoff_span_ids: tuple[str, ...]
    retrieve_span_ids_by_source: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RunView:
    """Per-split view over all sessions (V2 donors must be co-split)."""

    sessions: tuple[SessionView, ...]


@dataclass(frozen=True, slots=True)
class FaultContext:
    """Everything a fault function may consult. Nothing else."""

    seed: int
    entry: CatalogueEntry
    session: SessionView
    run: RunView
    variant_key: str
    donor_decision_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InjectionResult:
    """Surgered rows (pre-renumber) plus the ground-truth draft."""

    rows: SessionRows
    draft: ViolationDraft


Injector = "Callable[[SessionRows, FaultContext], InjectionResult]"


@dataclass(frozen=True, slots=True)
class Registration:
    fn: Callable[[SessionRows, FaultContext], InjectionResult]
    eligible: Callable[[SessionView], bool]


REGISTRY: dict[str, Registration] = {}


FaultFn = "Callable[[SessionRows, FaultContext], InjectionResult]"


def register(
    name: str, *, eligible: Callable[[SessionView], bool]
) -> Callable[
    [Callable[[SessionRows, FaultContext], InjectionResult]],
    Callable[[SessionRows, FaultContext], InjectionResult],
]:
    """Register a fault function. Held-out classes (firewall §7a) later ship
    as new registered functions plus a separate catalogue file — zero edits
    to the frozen ``violations.yaml`` or the existing injectors."""

    def wrap(
        fn: Callable[[SessionRows, FaultContext], InjectionResult],
    ) -> Callable[[SessionRows, FaultContext], InjectionResult]:
        REGISTRY[name] = Registration(fn=fn, eligible=eligible)
        return fn

    return wrap


# ---------------------------------------------------------------------------
# Surgery helpers — the only way attributes change
# ---------------------------------------------------------------------------

_AT_PREFIXES: Final[tuple[str, ...]] = ("at.event.", "at.obj.", "at.o2o.")


def _spec_of(row: SpanRow) -> EventSpec | None:
    attrs = row.get("attributes")
    if not isinstance(attrs, dict):
        return None
    return parse_event(attrs)


def _mutate_event(row: SpanRow, fn: Callable[[EventSpec], EventSpec]) -> SpanRow:
    """Rewrite one event span's ``at.*`` encoding through the contract."""
    spec = _spec_of(row)
    if spec is None:
        raise InjectionError(f"span {row.get('span_id')} is not a layer-A event span")
    new_spec = fn(spec)
    validate_event(new_spec)
    base = {
        key: value for key, value in row["attributes"].items() if not key.startswith(_AT_PREFIXES)
    }
    base.update(flatten_event(new_spec))
    new_row = dict(row)
    new_row["attributes"] = base
    return new_row


def _delete_spans(rows: SessionRows, span_ids: frozenset[str]) -> SessionRows:
    return tuple(row for row in rows if row["span_id"] not in span_ids)


def _renumber_seq(rows: SessionRows) -> SessionRows:
    """Dense 1..N over surviving events, in old-seq order. Event ids keep
    their pre-injection numeric infix by design (documented in labels)."""
    events = sorted(
        (row for row in rows if _spec_of(row) is not None),
        key=lambda row: int(row["attributes"][EVENT_SEQ]),
    )
    new_seq = {row["span_id"]: index for index, row in enumerate(events, start=1)}
    out: list[SpanRow] = []
    for row in rows:
        if row["span_id"] in new_seq:
            target = new_seq[row["span_id"]]
            if int(row["attributes"][EVENT_SEQ]) != target:

                def _set_seq(spec: EventSpec, t: int = target) -> EventSpec:
                    return replace(spec, seq=t)

                row = _mutate_event(row, _set_seq)
        out.append(row)
    return tuple(out)


def _recompute_session_end(rows: SessionRows) -> SessionRows:
    """``event_count`` = surviving layer-A events, so deletions look native.
    ``duration_seconds`` is untouched: deletions never move session_end's
    simulated time."""
    count = sum(1 for row in rows if _spec_of(row) is not None)
    out: list[SpanRow] = []
    for row in rows:
        spec = _spec_of(row)
        if spec is not None and spec.event_type is EventType.SESSION_END:
            current = dict(spec.attributes)
            if current.get("event_count") != count:

                def _set_count(s: EventSpec, c: int = count) -> EventSpec:
                    return replace(
                        s,
                        attributes=tuple(
                            (name, c if name == "event_count" else value)
                            for name, value in s.attributes
                        ),
                    )

                row = _mutate_event(row, _set_count)
        out.append(row)
    return tuple(out)


def _events_by_type(rows: SessionRows, event_type: EventType) -> list[tuple[SpanRow, EventSpec]]:
    found: list[tuple[SpanRow, EventSpec]] = []
    for row in rows:
        spec = _spec_of(row)
        if spec is not None and spec.event_type is event_type:
            found.append((row, spec))
    return found


def _session_view(rows: SessionRows) -> SessionView:
    session_id = ""
    for row in rows:
        attrs = row.get("attributes")
        if isinstance(attrs, dict):
            scope = parse_scope(attrs)
            if scope is not None:
                session_id = scope.session_id
                break
    decisions = _events_by_type(rows, EventType.MAKE_DECISION)
    if len(decisions) != 1:
        raise InjectionError(f"session {session_id!r} has {len(decisions)} make_decision events")
    _, decision_spec = decisions[0]
    decision_attrs = dict(decision_spec.attributes)
    if "outcome" not in decision_attrs:
        raise InjectionError(
            f"session {session_id!r}: make_decision carries no outcome attribute; "
            "this is not a well-formed base run"
        )
    outcome = str(decision_attrs["outcome"])
    decision_id = next(
        ref.object_id
        for ref in decision_spec.objects
        if ref.object_type is ObjectType.CREDIT_DECISION
    )
    grants = _events_by_type(rows, EventType.GRANT_APPROVAL)
    denies = _events_by_type(rows, EventType.DENY_APPROVAL)
    approver_id: str | None = None
    for _, spec in grants + denies:
        approver_id = spec.actor_id
    handoffs = tuple(row["span_id"] for row, _ in _events_by_type(rows, EventType.HANDOFF))
    retrieves: list[tuple[str, str]] = []
    for row, spec in _events_by_type(rows, EventType.RETRIEVE_DATA):
        attrs = dict(spec.attributes)
        if "resource_type" not in attrs:
            raise InjectionError(
                f"session {session_id!r}: retrieve_data span {row['span_id']} carries "
                "no resource_type attribute; this is not a well-formed base run"
            )
        retrieves.append((str(attrs["resource_type"]), str(row["span_id"])))
    return SessionView(
        session_id=session_id,
        outcome=outcome,
        decision_id=decision_id,
        has_grant_approval=bool(grants),
        approver_id=approver_id,
        handoff_span_ids=handoffs,
        retrieve_span_ids_by_source=tuple(retrieves),
    )


def _pick(variant_key: str, salt: str, options: Sequence[str]) -> str:
    """Deterministic option choice: rank options by digest, take the first."""
    return min(options, key=lambda option: _rank(variant_key, salt, option))


# ---------------------------------------------------------------------------
# Fault functions V1..V8
# ---------------------------------------------------------------------------


def _approval_events(rows: SessionRows) -> list[tuple[SpanRow, EventSpec]]:
    return (
        _events_by_type(rows, EventType.REQUEST_APPROVAL)
        + _events_by_type(rows, EventType.GRANT_APPROVAL)
        + _events_by_type(rows, EventType.DENY_APPROVAL)
    )


@register("fault_v1", eligible=lambda s: s.outcome in ("grant", "deny"))
def fault_v1(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Delete the approval events entirely; strip APRV refs from the decision."""
    approvals = _approval_events(rows)
    removed = frozenset(row["span_id"] for row, _ in approvals)
    approval_object_ids = {
        ref.object_id
        for _, spec in approvals
        for ref in spec.objects
        if ref.object_type is ObjectType.APPROVAL
    }
    rows = _delete_spans(rows, removed)
    decision_row, decision_spec = _events_by_type(rows, EventType.MAKE_DECISION)[0]

    def strip(spec: EventSpec) -> EventSpec:
        return replace(
            spec,
            objects=tuple(ref for ref in spec.objects if ref.object_id not in approval_object_ids),
        )

    new_rows = tuple(
        _mutate_event(row, strip) if row["span_id"] == decision_row["span_id"] else row
        for row in rows
    )
    return InjectionResult(
        rows=new_rows,
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=(decision_spec.event_id,),
            ocel_object_ids=(ctx.session.decision_id,),
            expected_evidence_span_ids=(decision_row["span_id"],),
            removed_span_ids=tuple(sorted(removed)),
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=ctx.entry.description,
        ),
    )


# Outcome-gated like V1/V3: a refer decision carries no approval obligation
# (the catalogue's clean-split-safety rule), so a repointed approval in a
# refer session would be ground truth the pre-registered T1 semantics cannot
# flag. Surfaced by the Phase 5 recall gate, 19 Aug 2026.
@register("fault_v2", eligible=lambda s: s.has_grant_approval and s.outcome in ("grant", "deny"))
def fault_v2(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Repoint the approval's `approves` relation at a donor session's decision."""
    donors = [d for d in ctx.donor_decision_ids if d != ctx.session.decision_id]
    if not donors:
        raise InjectionError(
            f"V2 on {ctx.session.session_id}: no co-split donor decision available"
        )
    donor = _pick(ctx.variant_key, "donor", donors)
    approval_row, approval_spec = _events_by_type(rows, EventType.GRANT_APPROVAL)[0]
    decision_row, decision_spec = _events_by_type(rows, EventType.MAKE_DECISION)[0]

    def repoint(spec: EventSpec) -> EventSpec:
        return replace(
            spec,
            objects=tuple(
                replace(ref, object_id=donor) if ref.qualifier is Qualifier.APPROVES else ref
                for ref in spec.objects
            ),
        )

    new_rows = tuple(
        _mutate_event(row, repoint) if row["span_id"] == approval_row["span_id"] else row
        for row in rows
    )
    return InjectionResult(
        rows=new_rows,
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=(approval_spec.event_id, decision_spec.event_id),
            ocel_object_ids=(ctx.session.decision_id, donor),
            expected_evidence_span_ids=(approval_row["span_id"], decision_row["span_id"]),
            removed_span_ids=(),
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=ctx.entry.description,
        ),
    )


_ROGUE_ROLES: Final[tuple[str, ...]] = ("intern", "contractor", "trainee_analyst")


@register("fault_v3", eligible=lambda s: s.outcome in ("grant", "deny"))
def fault_v3(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Swap in an out-of-roster approver, declared only in this session."""
    rogue_role = _pick(ctx.variant_key, "role", _ROGUE_ROLES)
    rogue_id = f"APR-9{ctx.variant_key[:2]}-{ctx.session.session_id}"
    rogue_attrs = (
        ("approver_id", rogue_id),
        ("authority_level", 0),
        ("role", rogue_role),
    )

    request_row, _ = _events_by_type(rows, EventType.REQUEST_APPROVAL)[0]
    verdicts = _events_by_type(rows, EventType.GRANT_APPROVAL) + _events_by_type(
        rows, EventType.DENY_APPROVAL
    )
    verdict_row, verdict_spec = verdicts[0]

    def swap_request(spec: EventSpec) -> EventSpec:
        return replace(
            spec,
            objects=tuple(
                replace(
                    ref,
                    object_id=rogue_id,
                    attributes=rogue_attrs if ref.declares else (),
                )
                if ref.object_type is ObjectType.HUMAN_APPROVER
                else ref
                for ref in spec.objects
            ),
        )

    def swap_verdict(spec: EventSpec) -> EventSpec:
        new_attrs = tuple(
            (name, rogue_role)
            if name == "approver_role"
            else ((name, 0) if name == "authority_level" else (name, value))
            for name, value in spec.attributes
        )
        new_objects = []
        for ref in spec.objects:
            if ref.object_type is ObjectType.HUMAN_APPROVER:
                ref = replace(ref, object_id=rogue_id)
            elif ref.object_type is ObjectType.APPROVAL and ref.declares:
                ref = replace(
                    ref,
                    attributes=tuple(
                        (name, rogue_role) if name == "approver_role" else (name, value)
                        for name, value in ref.attributes
                    ),
                )
            new_objects.append(ref)
        return replace(spec, actor_id=rogue_id, attributes=new_attrs, objects=tuple(new_objects))

    new_rows = []
    for row in rows:
        if row["span_id"] == request_row["span_id"]:
            new_rows.append(_mutate_event(row, swap_request))
        elif row["span_id"] == verdict_row["span_id"]:
            new_rows.append(_mutate_event(row, swap_verdict))
        else:
            new_rows.append(row)
    return InjectionResult(
        rows=tuple(new_rows),
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=(verdict_spec.event_id,),
            ocel_object_ids=(rogue_id,),
            # Both mutated spans: the request span carries the rogue
            # approver's only attribute declaration (review finding — the
            # labels must account for all mutated telemetry).
            expected_evidence_span_ids=(request_row["span_id"], verdict_row["span_id"]),
            removed_span_ids=(),
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=f"{ctx.entry.description} (rogue_role: {rogue_role})",
        ),
    )


@register("fault_v4", eligible=lambda s: True)
def fault_v4(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Delete one required-source retrieval (leaf, or the whole tool call)."""
    sources = sorted({source for source, _ in ctx.session.retrieve_span_ids_by_source})
    required = [s for s in sources if s != "applicant_demographics"]
    source = _pick(ctx.variant_key, "source", required)
    retrieve_span_id = dict(ctx.session.retrieve_span_ids_by_source)[source]
    delete_mode = _pick(ctx.variant_key, "mode", ("leaf", "tool_call"))

    _retrieve_row, retrieve_spec = next(
        (row, spec)
        for row, spec in _events_by_type(rows, EventType.RETRIEVE_DATA)
        if row["span_id"] == retrieve_span_id
    )
    removed = {retrieve_span_id}
    if delete_mode == "tool_call":
        # The call_tool immediately preceding the retrieval, plus its
        # layer-B child — deleting the call_tool alone orphans the child.
        target_seq = retrieve_spec.seq - 1
        tool_row = next(
            row
            for row, spec in _events_by_type(rows, EventType.CALL_TOOL)
            if spec.seq == target_seq
        )
        removed.add(tool_row["span_id"])
        removed.update(
            row["span_id"] for row in rows if row.get("parent_span_id") == tool_row["span_id"]
        )
    decision_row, decision_spec = _events_by_type(rows, EventType.MAKE_DECISION)[0]
    return InjectionResult(
        rows=_delete_spans(rows, frozenset(removed)),
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=(decision_spec.event_id,),
            ocel_object_ids=(),
            expected_evidence_span_ids=(decision_row["span_id"],),
            removed_span_ids=tuple(sorted(removed)),
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=f"{ctx.entry.description} (source: {source}, mode: {delete_mode})",
        ),
    )


@register("fault_v5", eligible=lambda s: True)
def fault_v5(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Session-local demographics resource with an empty lawful_basis."""
    local_id = f"RES-DEMOGRAPHICS-{ctx.session.session_id}"
    target_row: SpanRow | None = None
    target_spec: EventSpec | None = None
    for row, spec in _events_by_type(rows, EventType.RETRIEVE_DATA):
        if dict(spec.attributes).get("resource_type") == "applicant_demographics":
            target_row, target_spec = row, spec
            break
    if target_row is None or target_spec is None:
        raise InjectionError(f"V5 on {ctx.session.session_id}: no demographics retrieval found")

    def localise(spec: EventSpec) -> EventSpec:
        return replace(
            spec,
            objects=tuple(
                replace(
                    ref,
                    object_id=local_id,
                    attributes=tuple(
                        (name, "" if name == "lawful_basis" else value)
                        for name, value in ref.attributes
                    )
                    if ref.declares
                    else (),
                )
                if ref.object_type is ObjectType.DATA_RESOURCE
                else ref
                for ref in spec.objects
            ),
        )

    new_rows = tuple(
        _mutate_event(row, localise) if row["span_id"] == target_row["span_id"] else row
        for row in rows
    )
    return InjectionResult(
        rows=new_rows,
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=(target_spec.event_id,),
            ocel_object_ids=(local_id,),
            expected_evidence_span_ids=(target_row["span_id"],),
            removed_span_ids=(),
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=ctx.entry.description,
        ),
    )


@register("fault_v6", eligible=lambda s: s.outcome == "deny")
def fault_v6(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Blank the CreditDecision OBJECT's reason_codes (variant A) or strip
    the emit_reasoning explains edge (variant B). Never the event attr."""
    variant = _pick(ctx.variant_key, "variant", ("blank_object_reason_codes", "strip_explains"))
    decision_row, decision_spec = _events_by_type(rows, EventType.MAKE_DECISION)[0]
    evidence: tuple[str, ...]

    if variant == "blank_object_reason_codes":

        def blank(spec: EventSpec) -> EventSpec:
            return replace(
                spec,
                objects=tuple(
                    replace(
                        ref,
                        attributes=tuple(
                            (name, () if name == "reason_codes" else value)
                            for name, value in ref.attributes
                        ),
                    )
                    if ref.object_type is ObjectType.CREDIT_DECISION and ref.declares
                    else ref
                    for ref in spec.objects
                ),
            )

        new_rows = tuple(
            _mutate_event(row, blank) if row["span_id"] == decision_row["span_id"] else row
            for row in rows
        )
        evidence = (decision_row["span_id"],)
    else:
        # Strip the explains edge on the post-decision emit_reasoning.
        explainers = [
            (row, spec)
            for row, spec in _events_by_type(rows, EventType.EMIT_REASONING)
            if any(
                ref.qualifier is Qualifier.EXPLAINS and ref.object_id == ctx.session.decision_id
                for ref in spec.objects
            )
            and spec.seq > decision_spec.seq
        ]
        if not explainers:
            raise InjectionError(f"V6 on {ctx.session.session_id}: no post-decision explains edge")
        explain_row, _ = explainers[0]

        def strip(spec: EventSpec) -> EventSpec:
            return replace(
                spec,
                objects=tuple(
                    ref
                    for ref in spec.objects
                    if not (
                        ref.qualifier is Qualifier.EXPLAINS
                        and ref.object_id == ctx.session.decision_id
                    )
                ),
            )

        new_rows = tuple(
            _mutate_event(row, strip) if row["span_id"] == explain_row["span_id"] else row
            for row in rows
        )
        evidence = (decision_row["span_id"], explain_row["span_id"])

    return InjectionResult(
        rows=new_rows,
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=(decision_spec.event_id,),
            ocel_object_ids=(ctx.session.decision_id,),
            expected_evidence_span_ids=evidence,
            removed_span_ids=(),
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=f"{ctx.entry.description} (variant: {variant})",
        ),
    )


@register("fault_v7", eligible=lambda s: True)
def fault_v7(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Delete one handoff (variant A) or repoint its `to` (variant B)."""
    mode = _pick(ctx.variant_key, "mode", ("delete", "repoint_to"))
    handoff_span_id = _pick(ctx.variant_key, "which", ctx.session.handoff_span_ids)
    handoff_row, handoff_spec = next(
        (row, spec)
        for row, spec in _events_by_type(rows, EventType.HANDOFF)
        if row["span_id"] == handoff_span_id
    )
    to_ref = next(ref for ref in handoff_spec.objects if ref.qualifier is Qualifier.TO)
    orphaned_agent = to_ref.object_id
    # The invoke_agent that follows this handoff is the orphaned action.
    orphan_row, orphan_spec = next(
        (row, spec)
        for row, spec in _events_by_type(rows, EventType.INVOKE_AGENT)
        if spec.seq == handoff_spec.seq + 1
    )
    removed: tuple[str, ...]
    evidence: tuple[str, ...]
    event_ids: tuple[str, ...]

    if mode == "delete":
        new_rows = _delete_spans(rows, frozenset({handoff_span_id}))
        removed = (handoff_span_id,)
        evidence = (orphan_row["span_id"],)
        event_ids = (orphan_spec.event_id,)
    else:
        from_ref = next(ref for ref in handoff_spec.objects if ref.qualifier is Qualifier.FROM)
        # The wrong recipient: any other agent in the session that is neither
        # the sender nor the intended recipient (the same agent twice in one
        # event would violate the contract).
        session_agents = sorted(
            {
                spec.actor_id
                for _, spec in _events_by_type(rows, EventType.INVOKE_AGENT)
                if spec.actor_id is not None
            }
            - {from_ref.object_id, orphaned_agent}
        )
        if not session_agents:
            raise InjectionError(f"V7 on {ctx.session.session_id}: no third agent to misdirect to")
        wrong_to = _pick(ctx.variant_key, "wrong_to", session_agents)

        def repoint(spec: EventSpec) -> EventSpec:
            return replace(
                spec,
                objects=tuple(
                    replace(ref, object_id=wrong_to) if ref.qualifier is Qualifier.TO else ref
                    for ref in spec.objects
                ),
                o2o=tuple(
                    replace(rel, target_id=wrong_to)
                    if rel.qualifier is Qualifier.DELEGATES_TO and rel.target_id == orphaned_agent
                    else rel
                    for rel in spec.o2o
                ),
            )

        new_rows = tuple(
            _mutate_event(row, repoint) if row["span_id"] == handoff_span_id else row
            for row in rows
        )
        removed = ()
        evidence = (handoff_row["span_id"], orphan_row["span_id"])
        event_ids = (handoff_spec.event_id, orphan_spec.event_id)

    return InjectionResult(
        rows=new_rows,
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=event_ids,
            ocel_object_ids=(orphaned_agent,),
            expected_evidence_span_ids=evidence,
            removed_span_ids=removed,
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=f"{ctx.entry.description} (mode: {mode})",
        ),
    )


# ---------------------------------------------------------------------------
# Distractors (B2 near-miss suite; never counted as violations)
# ---------------------------------------------------------------------------

#: APR-003's declaration, mirrored from data/catalogue/scenario_credit.yaml
#: (drift-guarded by test). In-roster and legal, but never used naturally —
#: swapping it in creates the approval-at-the-authority-boundary near-miss.
_D1_APPROVER: Final[tuple[tuple[str, object], ...]] = (
    ("approver_id", "APR-003"),
    ("authority_level", 3),
    ("role", "risk_manager"),
)


@dataclass(frozen=True, slots=True)
class Distractor:
    """A compliant near-miss, recorded so FP analysis can find it."""

    distractor_id: str
    kind: str
    session_id: str
    span_ids: tuple[str, ...]
    description: str


def _apply_d1(rows: SessionRows, view: SessionView) -> tuple[SessionRows, tuple[str, ...]]:
    """Swap the approver to APR-003 (risk_manager) — legal but unusual."""
    apr_id = str(_D1_APPROVER[0][1])
    role = str(_D1_APPROVER[2][1])
    level = int(str(_D1_APPROVER[1][1]))

    request_row, _ = _events_by_type(rows, EventType.REQUEST_APPROVAL)[0]
    verdicts = _events_by_type(rows, EventType.GRANT_APPROVAL) + _events_by_type(
        rows, EventType.DENY_APPROVAL
    )
    verdict_row, _ = verdicts[0]

    def swap_request(spec: EventSpec) -> EventSpec:
        return replace(
            spec,
            objects=tuple(
                replace(
                    ref,
                    object_id=apr_id,
                    attributes=_D1_APPROVER if ref.declares else (),  # type: ignore[arg-type]
                )
                if ref.object_type is ObjectType.HUMAN_APPROVER
                else ref
                for ref in spec.objects
            ),
        )

    def swap_verdict(spec: EventSpec) -> EventSpec:
        new_attrs = tuple(
            (name, role)
            if name == "approver_role"
            else ((name, level) if name == "authority_level" else (name, value))
            for name, value in spec.attributes
        )
        new_objects = []
        for ref in spec.objects:
            if ref.object_type is ObjectType.HUMAN_APPROVER:
                ref = replace(ref, object_id=apr_id)
            elif ref.object_type is ObjectType.APPROVAL and ref.declares:
                ref = replace(
                    ref,
                    attributes=tuple(
                        (name, role) if name == "approver_role" else (name, value)
                        for name, value in ref.attributes
                    ),
                )
            new_objects.append(ref)
        return replace(spec, actor_id=apr_id, attributes=new_attrs, objects=tuple(new_objects))

    new_rows = []
    for row in rows:
        if row["span_id"] == request_row["span_id"]:
            new_rows.append(_mutate_event(row, swap_request))
        elif row["span_id"] == verdict_row["span_id"]:
            new_rows.append(_mutate_event(row, swap_verdict))
        else:
            new_rows.append(row)
    return tuple(new_rows), (request_row["span_id"], verdict_row["span_id"])


@register("fault_v8", eligible=lambda s: True)
def fault_v8(rows: SessionRows, ctx: FaultContext) -> InjectionResult:
    """Repoint the decision's governed_by at the superseded policy."""
    decision_row, decision_spec = _events_by_type(rows, EventType.MAKE_DECISION)[0]
    _session_start_row, session_start_spec = _events_by_type(rows, EventType.SESSION_START)[0]
    policies = {
        ref.object_id
        for ref in session_start_spec.objects
        if ref.object_type is ObjectType.POLICY_VERSION
    }
    current = next(
        ref.object_id
        for ref in decision_spec.objects
        if ref.object_type is ObjectType.POLICY_VERSION
    )
    superseded = sorted(policies - {current})
    if not superseded:
        raise InjectionError(f"V8 on {ctx.session.session_id}: no superseded policy declared")
    target = superseded[0]

    def repoint(spec: EventSpec) -> EventSpec:
        return replace(
            spec,
            objects=tuple(
                replace(ref, object_id=target)
                if ref.object_type is ObjectType.POLICY_VERSION
                else ref
                for ref in spec.objects
            ),
        )

    new_rows = tuple(
        _mutate_event(row, repoint) if row["span_id"] == decision_row["span_id"] else row
        for row in rows
    )
    return InjectionResult(
        rows=new_rows,
        draft=ViolationDraft(
            fault_class=ctx.entry.entry_id,
            constraint_id=ctx.entry.constraint_id,
            session_id=ctx.session.session_id,
            ocel_event_ids=(decision_spec.event_id,),
            ocel_object_ids=(target,),
            expected_evidence_span_ids=(decision_row["span_id"],),
            removed_span_ids=(),
            severity=ctx.entry.severity,
            articles=ctx.entry.articles,
            description=ctx.entry.description,
        ),
    )


# ---------------------------------------------------------------------------
# Splits, writers, and the orchestrator
# ---------------------------------------------------------------------------

#: The documented event-id fact every labels file carries.
EVENT_ID_NOTE: Final[str] = (
    "event ids are stable across injection; after a deletion their numeric "
    "infix is the PRE-injection at.event.seq, while at.event.seq itself is "
    "renumbered densely"
)


def _scarcity_order(
    entries: Sequence[CatalogueEntry],
    session_ids: Sequence[str],
    views: Mapping[str, SessionView],
) -> tuple[CatalogueEntry, ...]:
    """Catalogue-driven scarcity order: smallest eligibility pool first.

    Computed from the actual sessions rather than hard-coded, so a held-out
    catalogue with extra classes allocates correctly with zero code edits
    (the register() extension seam) — a hard-coded V1..V8 tuple silently
    under-injected additional entries (review finding, 19 Aug 2026).
    """

    def pool(entry: CatalogueEntry) -> int:
        return sum(
            1 for sid in session_ids if REGISTRY[entry.injection_function].eligible(views[sid])
        )

    return tuple(sorted(entries, key=lambda e: (pool(e), e.entry_id)))


def _t1_classes(catalogue: Catalogue) -> frozenset[str]:
    """Classes sharing T1: pairwise-excluded and barred from V2 donorship."""
    return frozenset(e.entry_id for e in catalogue.entries if e.constraint_id.startswith("T1."))


_MANIFEST_KEYS: Final[tuple[str, ...]] = (
    "catalogue_sha256",
    "contract",
    "files",
    "genai_semconv",
    "model_id",
    "provider",
    "run_id",
    "scenario_name",
    "scenario_version",
    "schema_version",
    "seed",
    "seed_data_sha256",
    "session_count",
)


@dataclass(frozen=True, slots=True)
class LabelsFile:
    """The ground-truth sidecar for one split."""

    schema_version: int
    split: str
    base_run_id: str
    injector_seed: int
    violations_catalogue_sha256: str
    catalogue_version: str
    event_id_note: str
    violations: tuple[GroundTruthViolation, ...]
    distractors: tuple[Distractor, ...]


def _read_base_manifest(span_dir: Path) -> dict[str, Any]:
    manifest_path = span_dir / "manifest.json"
    if not manifest_path.exists():
        raise InjectionInputMissingError(f"no such manifest: {manifest_path}")
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise InjectionError(f"{manifest_path}: manifest is not an object")
    missing = set(_MANIFEST_KEYS) - set(doc)
    if missing:
        raise InjectionError(f"{manifest_path}: missing manifest key(s) {sorted(missing)}")
    if doc["contract"] != "at-span/1":
        raise InjectionError(f"{manifest_path}: contract {doc['contract']!r} is not 'at-span/1'")
    return doc


def _load_session_rows(path: Path) -> SessionRows:
    rows = tuple(
        json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()
    )
    return tuple(sorted(rows, key=lambda r: int(r["creation_index"])))


def _serialise_rows(rows: SessionRows) -> bytes:
    return "".join(_canonical_json(row) + "\n" for row in rows).encode("utf-8")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _write_split_manifest(
    out_dir: Path, base_manifest: dict[str, Any], files: list[tuple[str, str]]
) -> Path:
    """A core-only mirror of ``scenario/telemetry.write_manifest`` — byte
    drift is guarded by a test that runs both writers on one payload."""
    payload = {key: base_manifest[key] for key in _MANIFEST_KEYS}
    payload["files"] = [list(pair) for pair in sorted(files)]
    payload["session_count"] = len(files)
    path = out_dir / "manifest.json"
    _atomic_write_bytes(path, (_canonical_json(payload) + "\n").encode("utf-8"))
    return path


def write_labels(path: Path, labels: LabelsFile) -> Path:
    """Write the ground-truth sidecar as one canonical-JSON line."""
    payload = {
        "schema_version": labels.schema_version,
        "split": labels.split,
        "base_run_id": labels.base_run_id,
        "injector_seed": labels.injector_seed,
        "violations_catalogue_sha256": labels.violations_catalogue_sha256,
        "catalogue_version": labels.catalogue_version,
        "event_id_note": labels.event_id_note,
        "violations": [
            {
                "violation_id": v.violation_id,
                "fault_class": v.fault_class,
                "constraint_id": v.constraint_id,
                "session_id": v.session_id,
                "ocel_event_ids": list(v.ocel_event_ids),
                "ocel_object_ids": list(v.ocel_object_ids),
                "expected_evidence_span_ids": list(v.expected_evidence_span_ids),
                "removed_span_ids": list(v.removed_span_ids),
                "severity": v.severity,
                "articles": list(v.articles),
                "description": v.description,
            }
            for v in sorted(labels.violations, key=lambda v: (v.session_id, v.fault_class))
        ],
        "distractors": [
            {
                "distractor_id": d.distractor_id,
                "kind": d.kind,
                "session_id": d.session_id,
                "span_ids": list(d.span_ids),
                "description": d.description,
            }
            for d in sorted(labels.distractors, key=lambda d: d.session_id)
        ],
    }
    _atomic_write_bytes(path, (_canonical_json(payload) + "\n").encode("utf-8"))
    return path


def read_labels(path: Path) -> LabelsFile:
    """Read a labels sidecar. Stdlib json only — no pyyaml required."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    return LabelsFile(
        schema_version=int(doc["schema_version"]),
        split=str(doc["split"]),
        base_run_id=str(doc["base_run_id"]),
        injector_seed=int(doc["injector_seed"]),
        violations_catalogue_sha256=str(doc["violations_catalogue_sha256"]),
        catalogue_version=str(doc["catalogue_version"]),
        event_id_note=str(doc["event_id_note"]),
        violations=tuple(
            GroundTruthViolation(
                violation_id=str(v["violation_id"]),
                fault_class=str(v["fault_class"]),
                constraint_id=str(v["constraint_id"]),
                session_id=str(v["session_id"]),
                ocel_event_ids=tuple(v["ocel_event_ids"]),
                ocel_object_ids=tuple(v["ocel_object_ids"]),
                expected_evidence_span_ids=tuple(v["expected_evidence_span_ids"]),
                removed_span_ids=tuple(v["removed_span_ids"]),
                severity=str(v["severity"]),
                articles=tuple(v["articles"]),
                description=str(v["description"]),
            )
            for v in doc["violations"]
        ),
        distractors=tuple(
            Distractor(
                distractor_id=str(d["distractor_id"]),
                kind=str(d["kind"]),
                session_id=str(d["session_id"]),
                span_ids=tuple(d["span_ids"]),
                description=str(d["description"]),
            )
            for d in doc["distractors"]
        ),
    )


def auto_split_sizes(total: int, class_count: int = 8) -> dict[Split, int]:
    """The default 1:4:1 partition: single = the largest class-divisible
    chunk of ~2/3 of the run; the remainder halves into clean and mixed.

    360 sessions -> clean 60 / single 240 / mixed 60 (the documented
    corpus); 50 -> 9/32/9; 16 -> 4/8/4 (the test fixture).
    """
    single = class_count * ((4 * total // 6) // class_count)
    if single == 0:
        raise InjectionError(
            f"a run of {total} sessions is too small for even one instance per "
            f"class ({class_count} classes); increase --n"
        )
    remainder = total - single
    clean = remainder // 2
    return {"clean": clean, "single": single, "mixed": remainder - clean}


def _partition(
    session_ids: list[str], seed: int, split_sizes: Mapping[Split, int]
) -> dict[Split, list[str]]:
    missing = [split for split in SPLITS if split not in split_sizes]
    if missing:
        raise InjectionError(
            f"split sizes must name all three splits; missing {missing} "
            "(pass an explicit 0 if a split is intentionally empty)"
        )
    total = sum(split_sizes.get(split, 0) for split in SPLITS)
    if total != len(session_ids):
        raise InjectionError(
            f"split sizes sum to {total} but the run has {len(session_ids)} sessions; "
            "sizes must partition the run exactly"
        )
    ranked = sorted(session_ids, key=lambda sid: _rank(seed, "split", sid))
    out: dict[Split, list[str]] = {}
    cursor = 0
    for split in SPLITS:
        count = split_sizes.get(split, 0)
        out[split] = sorted(ranked[cursor : cursor + count])
        cursor += count
    return out


def _allocate_single(
    session_ids: list[str],
    views: Mapping[str, SessionView],
    catalogue: Catalogue,
    seed: int,
) -> dict[str, list[str]]:
    """session_id -> [fault_class], exactly one class per session."""
    if len(session_ids) % len(catalogue.entries) != 0:
        raise InjectionError(
            f"single split size {len(session_ids)} is not divisible by "
            f"{len(catalogue.entries)} classes; choose a divisible size"
        )
    quota = len(session_ids) // len(catalogue.entries)
    unassigned = set(session_ids)
    assignment: dict[str, list[str]] = {}
    for entry in _scarcity_order(catalogue.entries, session_ids, views):
        eligible = [
            sid
            for sid in sorted(unassigned)
            if REGISTRY[entry.injection_function].eligible(views[sid])
        ]
        if len(eligible) < quota:
            raise InjectionError(
                f"class {entry.entry_id} needs {quota} eligible sessions in the "
                f"single split but only {len(eligible)} qualify; increase --n"
            )
        chosen = sorted(eligible, key=lambda sid: _rank(seed, entry.entry_id, sid))[:quota]
        for sid in chosen:
            assignment[sid] = [entry.entry_id]
            unassigned.discard(sid)
    return assignment


_MIXED_K_THRESHOLDS: Final[tuple[float, float, float]] = (0.15, 0.50, 0.85)


def _allocate_mixed(
    session_ids: list[str],
    views: Mapping[str, SessionView],
    catalogue: Catalogue,
    seed: int,
) -> dict[str, list[str]]:
    """session_id -> [fault_class, ...], 0..3 pairwise-compatible classes."""
    by_id = {e.entry_id: e for e in catalogue.entries}
    assignment: dict[str, list[str]] = {}
    for sid in sorted(session_ids):
        fraction = int(_rank(seed, "k", sid)[:8], 16) / 0xFFFFFFFF
        k = sum(1 for threshold in _MIXED_K_THRESHOLDS if fraction >= threshold)
        chosen: list[str] = []
        candidates = sorted(
            (e.entry_id for e in catalogue.entries),
            key=lambda entry_id: _rank(seed, "mix", sid, entry_id),
        )
        for entry_id in candidates:
            if len(chosen) == k:
                break
            entry = by_id[entry_id]
            if not REGISTRY[entry.injection_function].eligible(views[sid]):
                continue
            if any(entry_id not in by_id[c].co_injectable_with for c in chosen):
                continue
            chosen.append(entry_id)
        assignment[sid] = sorted(chosen)
    return assignment


def inject_run(
    span_dir: Path,
    out_dir: Path,
    seed: int,
    catalogue: Catalogue,
    split_sizes: Mapping[Split, int],
    distractor_count: int = 15,
) -> dict[Split, tuple[GroundTruthViolation, ...]]:
    """Produce the three labelled splits from one base run.

    A pure function of the base run's bytes, the seed, and the catalogue:
    running it twice produces byte-identical trees.
    """
    if distractor_count < 0:
        raise InjectionError(f"distractor_count must be >= 0, got {distractor_count}")
    manifest = _read_base_manifest(span_dir)
    if str(manifest["scenario_name"]) != catalogue.scenario_name:
        raise InjectionError(
            f"catalogue is bound to scenario {catalogue.scenario_name!r} but the run "
            f"is {manifest['scenario_name']!r}"
        )
    if str(manifest["scenario_version"]) != catalogue.scenario_version:
        raise InjectionError(
            f"catalogue is bound to scenario version {catalogue.scenario_version!r} "
            f"but the run is {manifest['scenario_version']!r}; fault surgery is "
            "built against one scenario structure"
        )

    file_by_session: dict[str, str] = {}
    raw_bytes: dict[str, bytes] = {}
    rows_by_session: dict[str, SessionRows] = {}
    views: dict[str, SessionView] = {}
    for filename, expected_digest in manifest["files"]:
        path = span_dir / str(filename)
        if not path.exists():
            raise InjectionInputMissingError(f"manifest lists {filename} but it does not exist")
        payload_bytes = path.read_bytes()
        digest = hashlib.sha256(payload_bytes).hexdigest()
        if digest != str(expected_digest):
            raise InjectionError(
                f"{filename}: sha256 mismatch against the base manifest; refusing "
                "to inject into a base run that is not intact"
            )
        rows = _load_session_rows(path)
        view = _session_view(rows)
        file_by_session[view.session_id] = str(filename)
        raw_bytes[view.session_id] = payload_bytes
        rows_by_session[view.session_id] = rows
        views[view.session_id] = view
    if len(views) != int(manifest["session_count"]):
        raise InjectionError(
            f"base manifest declares session_count={manifest['session_count']} but "
            f"{len(views)} sessions were read"
        )

    partition = _partition(sorted(views), seed, split_sizes)
    by_entry_id = {e.entry_id: e for e in catalogue.entries}
    t1_classes = _t1_classes(catalogue)

    assignments: dict[Split, dict[str, list[str]]] = {
        "clean": {sid: [] for sid in partition["clean"]},
        "single": _allocate_single(partition["single"], views, catalogue, seed),
        "mixed": _allocate_mixed(partition["mixed"], views, catalogue, seed),
    }
    for sid in partition["single"]:
        assignments["single"].setdefault(sid, [])

    # Validate V2 donor availability at ALLOCATION time — a mid-write failure
    # would otherwise leave a partial output tree (review finding).
    donor_ids_by_split: dict[Split, tuple[str, ...]] = {}
    for split in SPLITS:
        split_assignment = assignments[split]
        session_ids = partition[split]
        donors = tuple(
            views[sid].decision_id
            for sid in session_ids
            if not (t1_classes & set(split_assignment.get(sid, ())))
        )
        donor_ids_by_split[split] = donors
        for sid in session_ids:
            if "V2" in split_assignment.get(sid, ()) and not any(
                d != views[sid].decision_id for d in donors
            ):
                raise InjectionError(
                    f"V2 on {sid} in the {split} split has no eligible co-split "
                    "donor decision; use a different seed or a larger split"
                )

    results: dict[Split, tuple[GroundTruthViolation, ...]] = {}
    for split in SPLITS:
        split_assignment = assignments[split]
        session_ids = partition[split]
        donor_ids = donor_ids_by_split[split]

        # D1 distractors: approval sessions carrying no T1-class fault.
        if split == "clean" or distractor_count == 0:
            d1_sessions: set[str] = set()
        else:
            d1_pool = [
                sid
                for sid in session_ids
                if views[sid].outcome in ("grant", "deny")
                and not (t1_classes & set(split_assignment.get(sid, ())))
            ]
            d1_take = distractor_count if split == "single" else max(1, distractor_count // 4)
            d1_sessions = set(sorted(d1_pool, key=lambda sid: _rank(seed, "D1", sid))[:d1_take])

        violations: list[GroundTruthViolation] = []
        distractors: list[Distractor] = []
        # Write into a temp directory and swap it in whole, so a mid-run
        # failure can never leave a stale-but-self-consistent split behind.
        final_split = out_dir / split
        out_split = out_dir / f"{split}.tmp"
        if out_split.exists():
            import shutil

            shutil.rmtree(out_split)
        out_split.mkdir(parents=True, exist_ok=True)
        files: list[tuple[str, str]] = []

        for sid in session_ids:
            classes = split_assignment.get(sid, [])
            if not classes and sid not in d1_sessions:
                payload = raw_bytes[sid]
            else:
                rows = rows_by_session[sid]
                for entry_id in classes:
                    entry = by_entry_id[entry_id]
                    ctx = FaultContext(
                        seed=seed,
                        entry=entry,
                        session=views[sid],
                        run=RunView(sessions=tuple(views[s] for s in session_ids)),
                        variant_key=_rank(seed, entry_id, "variant", sid),
                        donor_decision_ids=donor_ids if entry_id == "V2" else (),
                    )
                    result = REGISTRY[entry.injection_function].fn(rows, ctx)
                    rows = result.rows
                    violations.append(
                        _finalise(result.draft, str(manifest["run_id"]), seed, catalogue.sha256)
                    )
                if sid in d1_sessions:
                    rows, d1_span_ids = _apply_d1(rows, views[sid])
                    distractors.append(
                        Distractor(
                            distractor_id=_rank(seed, "D1", "id", sid)[:16],
                            kind="D1_authority_boundary",
                            session_id=sid,
                            span_ids=d1_span_ids,
                            description=(
                                "approval by the highest-authority roster member "
                                "(risk_manager) on an ordinary application - legal "
                                "but unusual; a near-miss for T1"
                            ),
                        )
                    )
                rows = _recompute_session_end(_renumber_seq(rows))
                payload = _serialise_rows(rows)

            filename = file_by_session[sid]
            _atomic_write_bytes(out_split / filename, payload)
            files.append((filename, hashlib.sha256(payload).hexdigest()))

        # Natural near-miss distractors (B2): D2 refer-without-approval is
        # already present in clean telemetry wherever the human declined —
        # recorded so FP analysis can find it. (D3, the special-category read
        # WITH a documented basis, occurs in every session and is documented
        # in the catalogue rather than enumerated per session.)
        if split != "clean":
            for sid in session_ids:
                if views[sid].outcome == "refer":
                    distractors.append(
                        Distractor(
                            distractor_id=_rank(seed, "D2", "id", sid)[:16],
                            kind="D2_refer_without_approval",
                            session_id=sid,
                            span_ids=(),
                            description=(
                                "natural: the human approver declined and the "
                                "decision escalated to refer, which requires no "
                                "prior approval - a near-miss for T1"
                            ),
                        )
                    )

        _write_split_manifest(out_split, manifest, files)
        write_labels(
            out_split / "labels.json",
            LabelsFile(
                schema_version=1,
                split=split,
                base_run_id=str(manifest["run_id"]),
                injector_seed=seed,
                violations_catalogue_sha256=catalogue.sha256,
                catalogue_version=catalogue.catalogue_version,
                event_id_note=EVENT_ID_NOTE,
                violations=tuple(violations),
                distractors=tuple(distractors),
            ),
        )
        # Swap the finished split into place atomically (directory-level).
        if final_split.exists():
            import shutil

            shutil.rmtree(final_split)
        os.replace(out_split, final_split)
        results[split] = tuple(violations)
    return results

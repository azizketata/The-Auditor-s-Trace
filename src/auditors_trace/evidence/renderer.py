"""Violation to evidence record.

Phase 6 deliverable. Satisfies the technical half of claim C4 (each violation
renders into a defensible, reproducible evidence record).

No LLM call, ever. The natural-language constraint statement, legal bases and
standard references come from the crosswalk; the formal statement and version
from the ruleset — both authored by hand, cross-validated against each other.

The renderer never re-queries the log: everything a record cites arrives
through the prebuilt :class:`RenderIndex` — the span-index sidecar keyed by
event id, and per-session context derived once by :func:`build_render_index`
(semantic qualifiers only, never ``declares``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auditors_trace.constraints.engine import engine_version
from auditors_trace.constraints.ruleset import Rule, RuleSet
from auditors_trace.constraints.templates import Violation, build_index, related_ids
from auditors_trace.evidence.chain import chain, violation_id
from auditors_trace.evidence.crosswalk import Crosswalk, _entry, validate_against_ruleset
from auditors_trace.evidence.record import (
    RETENTION,
    Constraint,
    Context,
    EvidenceLocator,
    EvidenceRecord,
    Provenance,
    Reproducibility,
)
from auditors_trace.model.log import OCELLog
from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier

if TYPE_CHECKING:
    from collections.abc import Mapping

    from auditors_trace.ingest.mapper import SpanIndex


class RenderError(ValueError):
    """A violation cannot be rendered into a section 8 record."""


class UnmappedEventError(RenderError):
    """A cited event id is absent from the span index."""


class CrossTraceEvidenceError(RenderError):
    """Cited events span more than one trace; section 8's locator is singular."""


class UnknownRuleError(RenderError):
    """The violation's constraint id matches no rule in the ruleset."""


@dataclass(frozen=True)
class RenderIndex:
    """Everything the renderer may cite, prebuilt so it never touches the log."""

    #: Event id -> (trace id, the event's OWN span id — never enrichments, B1).
    event_span: Mapping[str, tuple[str, str]]
    #: Session id -> the section 8 context block for that session.
    session_context: Mapping[str, Context]


def build_render_index(log: OCELLog, span_index: SpanIndex) -> RenderIndex:
    """Derive per-event span refs and per-session context, once per log.

    Context rules (pre-registered at implementation, BUILD-PLAN Phase 6
    amendment): ``policy_version`` is the ``version`` attribute of the
    PolicyVersion the session's first ``make_decision`` is ``governed_by``
    ("" when the session has none); ``model_versions``/``agent_versions``
    collect ``{model_id: version}`` / ``{agent_id: version}`` over the
    session's events' SEMANTIC relations to Model/Agent objects (``uses``,
    ``performed_by``, ``from``, ``to``) — a ``declares`` materialisation
    relation never contributes context.
    """
    index = build_index(log)
    event_span = {ref.event_id: (ref.trace_id, ref.span_id) for ref in span_index.events}

    session_ids = sorted(set(index.session_of_event.values()))
    session_context: dict[str, Context] = {}
    for session_id in session_ids:
        events = tuple(
            event
            for event in index.events
            if index.session_of_event.get(event.event_id) == session_id
        )
        policy_version = ""
        for event in events:
            if event.event_type is not EventType.MAKE_DECISION:
                continue
            governed = related_ids(event, Qualifier.GOVERNED_BY, index, ObjectType.POLICY_VERSION)
            if governed:
                policy_version = str(index.object_attrs.get(governed[0], {}).get("version", ""))
                break
        model_versions: dict[str, str] = {}
        agent_versions: dict[str, str] = {}
        for event in events:
            for model_id in related_ids(event, Qualifier.USES, index, ObjectType.MODEL):
                attrs = index.object_attrs.get(model_id, {})
                model_versions[str(attrs.get("model_id", model_id))] = str(attrs.get("version", ""))
            for qualifier in (Qualifier.PERFORMED_BY, Qualifier.FROM, Qualifier.TO):
                for agent_id in related_ids(event, qualifier, index, ObjectType.AGENT):
                    attrs = index.object_attrs.get(agent_id, {})
                    agent_versions[str(attrs.get("agent_id", agent_id))] = str(
                        attrs.get("version", "")
                    )
        session_context[session_id] = Context(
            policy_version=policy_version,
            model_versions=dict(sorted(model_versions.items())),
            agent_versions=dict(sorted(agent_versions.items())),
        )
    return RenderIndex(event_span=event_span, session_context=session_context)


def _rule_for(ruleset: RuleSet, violation: Violation) -> Rule:
    for rule in ruleset.rules:
        if rule.constraint_id == violation.constraint_id:
            return rule
    raise UnknownRuleError(
        f"violation cites constraint {violation.constraint_id!r}, which no rule defines"
    )


def render(
    violation: Violation,
    *,
    crosswalk: Crosswalk,
    ruleset: RuleSet,
    render_index: RenderIndex,
    input_log_sha256: str,
    engine_commit: str = "",
) -> EvidenceRecord:
    """Render one violation as an unchained evidence record (section 8 exact)."""
    rule = _rule_for(ruleset, violation)
    if violation.severity is not rule.severity:
        raise RenderError(
            f"{violation.constraint_id}: violation severity {violation.severity.value!r} "
            f"disagrees with the rule's {rule.severity.value!r}"
        )
    entry = _entry(crosswalk, violation.constraint_id)

    traces: list[str] = []
    span_ids: list[str] = []
    for event_id in violation.ocel_event_ids:
        located = render_index.event_span.get(event_id)
        if located is None:
            raise UnmappedEventError(f"cited event {event_id!r} is absent from the span index")
        trace_id, span_id = located
        traces.append(trace_id)
        span_ids.append(span_id)
    if len(set(traces)) != 1:
        raise CrossTraceEvidenceError(
            f"{violation.constraint_id}: cited events span traces {sorted(set(traces))}; "
            "an evidence locator cites exactly one trace"
        )

    return EvidenceRecord(
        violation_id=violation_id(
            violation.constraint_id, violation.ocel_event_ids, input_log_sha256
        ),
        constraint=Constraint(
            id=violation.constraint_id,
            natural_language=entry.natural_language,
            formal=rule.formal,
            ruleset_version=ruleset.ruleset_version,
        ),
        legal_basis=entry.legal_basis,
        standard_refs=entry.standard_refs,
        severity=rule.severity,
        evidence=EvidenceLocator(
            ocel_event_ids=violation.ocel_event_ids,
            ocel_object_ids=violation.ocel_object_ids,
            otel_trace_id=traces[0],
            otel_span_ids=tuple(span_ids),
        ),
        context=render_index.session_context.get(violation.session_id, Context()),
        provenance=Provenance(
            engine_version=engine_version(),
            engine_commit=engine_commit,
            input_log_sha256=input_log_sha256,
        ),
        reproducibility=Reproducibility(
            rerun_command=(
                f"python -m auditors_trace.cli check --log {input_log_sha256} "
                f"--rules {ruleset.ruleset_version}"
            )
        ),
        retention=RETENTION,
    )


def render_all(
    violations: list[Violation],
    *,
    crosswalk: Crosswalk,
    ruleset: RuleSet,
    render_index: RenderIndex,
    input_log_sha256: str,
    engine_commit: str = "",
) -> list[EvidenceRecord]:
    """Render and chain a violation set into an ordered evidence log.

    Cross-validates the crosswalk against the ruleset once, then renders each
    violation and returns one hash chain (per rendered log).
    """
    validate_against_ruleset(crosswalk, ruleset)
    records = [
        render(
            violation,
            crosswalk=crosswalk,
            ruleset=ruleset,
            render_index=render_index,
            input_log_sha256=input_log_sha256,
            engine_commit=engine_commit,
        )
        for violation in violations
    ]
    return chain(records)

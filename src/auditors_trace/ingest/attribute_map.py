"""Map ``gen_ai.*`` and ``openinference.*`` / ``llm.*`` attributes onto one
canonical namespace, and build the C2 attribute-coverage report.

Phase 3 deliverable. Both vocabularies must produce identical canonical
output: the GenAI semantic conventions are still experimental and
instrumentation varies. Aliased keys present in both vocabularies must
agree — a value conflict raises :class:`IngestError`, because evidence with
two spellings of one fact is not evidence.

Every raw attribute key of a span lands in exactly one of three buckets
(the partition is unit-tested over every committed fixture span):

- ``consumed`` — folded into a canonical key (or the span-kind resolution);
- ``recorded`` — evidence-counted but never canonicalised (message bodies,
  LangGraph metadata blobs with volatile checkpoint UUIDs);
- ``unknown`` — everything else, recorded verbatim in the coverage report.
  Never silently dropped.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from auditors_trace.ingest.otel_reader import IngestError, ManifestView
from auditors_trace.model.log import canonical_json
from auditors_trace.model.span_contract import (
    SPAN_CONTRACT_VERSION,
    AttrValue,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from auditors_trace.model.ocel_schema import EventType

#: canonical key -> raw alias keys, exhaustive over STANDARD_GOVERNANCE_KEYS.
#: ``span.kind`` is special-cased (value mapping, not identity) and
#: ``invocation.*`` additionally read the ``llm.invocation_parameters`` blob.
CANONICAL_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "session.id": ("session.id", "gen_ai.conversation.id"),
    "model.name": ("llm.model_name", "gen_ai.request.model"),
    "model.provider": ("llm.provider", "gen_ai.provider.name"),
    "tokens.input": ("llm.token_count.prompt", "gen_ai.usage.input_tokens"),
    "tokens.output": ("llm.token_count.completion", "gen_ai.usage.output_tokens"),
    "tokens.total": ("llm.token_count.total",),
    "invocation.temperature": ("gen_ai.request.temperature",),
    "invocation.max_tokens": ("gen_ai.request.max_tokens",),
    "invocation.seed": ("gen_ai.request.seed",),
    "tool.name": ("tool.name", "gen_ai.tool.name"),
    "tool.description": ("tool.description", "gen_ai.tool.description"),
}

_KIND_KEY: Final[str] = "openinference.span.kind"
_OPERATION_KEY: Final[str] = "gen_ai.operation.name"
_INVOCATION_BLOB: Final[str] = "llm.invocation_parameters"
_OPERATION_TO_KIND: Final[dict[str, str]] = {
    "chat": "LLM",
    "execute_tool": "TOOL",
    "tool": "TOOL",
}
#: blob field -> canonical invocation key
_BLOB_FIELDS: Final[dict[str, str]] = {
    "temperature": "invocation.temperature",
    "max_tokens": "invocation.max_tokens",
    "seed": "invocation.seed",
}

#: Keys consumed as evidence but with no portable canonical form. Exact keys
#: and prefixes; counted "recorded", never canonicalised.
_RECORDED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "metadata",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.tool.call.result",
        "gen_ai.tool.type",
    }
)
_RECORDED_PREFIXES: Final[tuple[str, ...]] = (
    "input.",
    "output.",
    "llm.input_messages.",
    "llm.output_messages.",
    "gen_ai.response.",
)

#: kind -> canonical keys a fully-instrumented span of that kind carries.
#: ``invocation.seed`` is deliberately absent: the Claude API exposes no
#: sampling seed (documented deviation), so its presence is a bonus, never a
#: coverage requirement.
EXPECTED_BY_KIND: Final[dict[str, frozenset[str]]] = {
    "LLM": frozenset(
        {
            "session.id",
            "span.kind",
            "model.name",
            "model.provider",
            "invocation.temperature",
            "invocation.max_tokens",
            "tokens.input",
            "tokens.output",
            "tokens.total",
        }
    ),
    "TOOL": frozenset({"session.id", "span.kind", "tool.name", "tool.description"}),
    "CHAIN": frozenset({"session.id", "span.kind"}),
}


@dataclass(frozen=True, slots=True)
class NormalisedSpan:
    """One layer-B span folded onto the canonical namespace."""

    kind: str
    canonical: tuple[tuple[str, AttrValue], ...]
    consumed: tuple[str, ...]
    recorded: tuple[str, ...]
    unknown: tuple[str, ...]
    expected_present: tuple[str, ...]
    expected_missing: tuple[str, ...]


def _is_recorded(key: str) -> bool:
    return key in _RECORDED_KEYS or any(key.startswith(p) for p in _RECORDED_PREFIXES)


def _as_attr_value(source_key: str, raw: object) -> AttrValue:
    """Admit only representable values. Raised, never str()-laundered:
    ``None`` would become the string ``'None'`` and a dict a Python repr —
    both would silently enter the C2 coverage evidence."""
    if isinstance(raw, (str, bool, int, float)):
        return raw
    if isinstance(raw, (list, tuple)) and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    raise IngestError(
        f"attribute {source_key!r} carries an unrepresentable value {raw!r} "
        f"({type(raw).__name__}); only str/bool/int/float/sequence-of-str are evidence"
    )


def _require_token_int(canonical_key: str, sourced: list[tuple[str, AttrValue]]) -> None:
    for source_key, value in sourced:
        if isinstance(value, bool) or not isinstance(value, int):
            raise IngestError(
                f"token count {canonical_key!r} from {source_key!r} must be an "
                f"integer, got {value!r}; token arithmetic over non-integers "
                "would silently corrupt the coverage evidence"
            )


def _put(
    candidates: dict[str, list[tuple[str, AttrValue]]],
    canonical_key: str,
    source_key: str,
    value: AttrValue,
) -> None:
    candidates.setdefault(canonical_key, []).append((source_key, value))


def normalise_attributes(raw: Mapping[str, object]) -> NormalisedSpan:
    """Fold either vocabulary onto one canonical attribute namespace."""
    consumed: set[str] = set()
    #: canonical key -> [(source key, value), ...] for conflict detection
    candidates: dict[str, list[tuple[str, AttrValue]]] = {}

    for canonical_key, aliases in CANONICAL_ALIASES.items():
        for alias in aliases:
            if alias in raw:
                _put(candidates, canonical_key, alias, _as_attr_value(alias, raw[alias]))
                consumed.add(alias)

    # Span kind: explicit key, or derived from the gen_ai operation name.
    if _KIND_KEY in raw:
        _put(candidates, "span.kind", _KIND_KEY, str(raw[_KIND_KEY]))
        consumed.add(_KIND_KEY)
    if _OPERATION_KEY in raw:
        operation = str(raw[_OPERATION_KEY])
        consumed.add(_OPERATION_KEY)
        if operation in _OPERATION_TO_KIND:
            _put(candidates, "span.kind", _OPERATION_KEY, _OPERATION_TO_KIND[operation])

    # The invocation-parameters blob contributes its governance fields. A
    # blob that is not a JSON-object string is rejected — marking the key
    # consumed while discarding its fields would break the partition
    # semantics ("consumed = folded into a canonical key").
    if _INVOCATION_BLOB in raw:
        consumed.add(_INVOCATION_BLOB)
        blob_raw = raw[_INVOCATION_BLOB]
        if not isinstance(blob_raw, str):
            raise IngestError(
                f"llm.invocation_parameters must be a JSON string, got {type(blob_raw).__name__}"
            )
        try:
            blob = json.loads(blob_raw)
        except json.JSONDecodeError as exc:
            raise IngestError(f"llm.invocation_parameters is not valid JSON: {exc}") from exc
        if not isinstance(blob, dict):
            raise IngestError(f"llm.invocation_parameters must encode a JSON object, got {blob!r}")
        for field, canonical_key in _BLOB_FIELDS.items():
            if field in blob and blob[field] is not None:
                _put(
                    candidates,
                    canonical_key,
                    f"{_INVOCATION_BLOB}[{field}]",
                    _as_attr_value(f"{_INVOCATION_BLOB}[{field}]", blob[field]),
                )

    # Conflict detection: all sources of one canonical key must agree in
    # value AND type — Python's cross-type equality (True == 1 == 1.0) would
    # otherwise accept two different spellings of one fact.
    canonical: dict[str, AttrValue] = {}
    for canonical_key, sourced in candidates.items():
        if canonical_key.startswith("tokens."):
            _require_token_int(canonical_key, sourced)
        values = [value for _, value in sourced]
        first = values[0]
        if any(type(value) is not type(first) or value != first for value in values[1:]):
            detail = ", ".join(f"{src}={val!r}" for src, val in sourced)
            raise IngestError(
                f"conflicting values for canonical key {canonical_key!r}: {detail}; "
                "evidence with two spellings of one fact is not evidence"
            )
        canonical[canonical_key] = first

    # tokens.total: derive from input+output; an explicit total must match.
    # The operands are guaranteed non-bool ints by _require_token_int above.
    tokens_input = canonical.get("tokens.input")
    tokens_output = canonical.get("tokens.output")
    if isinstance(tokens_input, int) and isinstance(tokens_output, int):
        derived = tokens_input + tokens_output
        explicit = canonical.get("tokens.total")
        if explicit is not None and explicit != derived:
            raise IngestError(
                f"conflicting values for canonical key 'tokens.total': explicit "
                f"{explicit!r} but tokens.input+tokens.output = {derived}"
            )
        canonical["tokens.total"] = derived

    kind = str(canonical.get("span.kind", "UNKNOWN"))
    expected = EXPECTED_BY_KIND.get(kind, frozenset())
    present = frozenset(canonical) & expected

    recorded = tuple(sorted(k for k in raw if k not in consumed and _is_recorded(k)))
    unknown = tuple(sorted(k for k in raw if k not in consumed and not _is_recorded(k)))
    return NormalisedSpan(
        kind=kind,
        canonical=tuple(sorted(canonical.items())),
        consumed=tuple(sorted(consumed)),
        recorded=recorded,
        unknown=unknown,
        expected_present=tuple(sorted(present)),
        expected_missing=tuple(sorted(expected - present)),
    )


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Which governance-relevant attributes were mapped, and which were not.

    Serialised to ``results/e2_coverage.json``; the Phase 3 gate requires the
    mapped fraction to exceed 0.9. This is the evidence for claim C2. The
    layer-A denominator is ``governance_census`` — fixed per event type, so
    the gate cannot be gamed by shrinking the denominator.

    Read the two layers differently. The layer-A census fraction is 1.0 **by
    construction** whenever a run maps at all: every census key is mandatory
    for ``parse_event``/``validate_event``, so an under-instrumented layer-A
    span aborts the run (raise-never-repair) instead of lowering the number.
    The census term therefore documents *what full instrumentation means*;
    the layer-B term against ``EXPECTED_BY_KIND`` is the actually *measured*
    quantity — partial standard-vocabulary instrumentation degrades it
    without aborting.
    """

    schema_version: int
    event_count: int
    layer_b_span_count: int
    census_present: int
    census_total: int
    expected_present: int
    expected_total: int
    per_event_type: tuple[tuple[str, int, int], ...]
    per_kind: tuple[tuple[str, int, int], ...]
    missing_keys: tuple[tuple[str, int], ...]
    recorded_keys: tuple[tuple[str, int], ...]
    unknown_keys: tuple[tuple[str, int], ...]


def mapped_fraction(report: CoverageReport) -> float:
    """The C2 headline number: mapped over governance-relevant, both layers."""
    total = report.census_total + report.expected_total
    if total == 0:
        return 1.0
    return (report.census_present + report.expected_present) / total


def build_coverage(
    layer_a: Sequence[tuple[EventType, frozenset[str]]],
    layer_b: Sequence[NormalisedSpan],
) -> CoverageReport:
    """Aggregate per-run coverage from per-span observations.

    ``layer_a`` rows are ``(event_type, present census keys)`` — the caller
    intersects each event span's attribute keys with its census.
    """
    from auditors_trace.model.span_contract import governance_census

    census_present = 0
    census_total = 0
    per_event: dict[str, list[int]] = {}
    for event_type, present_keys in layer_a:
        census = governance_census(event_type)
        present = len(present_keys & set(census))
        census_present += present
        census_total += len(census)
        bucket = per_event.setdefault(event_type.value, [0, 0])
        bucket[0] += present
        bucket[1] += len(census)

    expected_present = 0
    expected_total = 0
    per_kind: dict[str, list[int]] = {}
    missing: Counter[str] = Counter()
    recorded: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for span in layer_b:
        expected_present += len(span.expected_present)
        expected_total += len(span.expected_present) + len(span.expected_missing)
        bucket = per_kind.setdefault(span.kind, [0, 0])
        bucket[0] += len(span.expected_present)
        bucket[1] += len(span.expected_present) + len(span.expected_missing)
        missing.update(span.expected_missing)
        recorded.update(span.recorded)
        unknown.update(span.unknown)

    return CoverageReport(
        schema_version=1,
        event_count=len(layer_a),
        layer_b_span_count=len(layer_b),
        census_present=census_present,
        census_total=census_total,
        expected_present=expected_present,
        expected_total=expected_total,
        per_event_type=tuple(
            (name, counts[0], counts[1]) for name, counts in sorted(per_event.items())
        ),
        per_kind=tuple((name, counts[0], counts[1]) for name, counts in sorted(per_kind.items())),
        missing_keys=tuple(sorted(missing.items())),
        recorded_keys=tuple(sorted(recorded.items())),
        unknown_keys=tuple(sorted(unknown.items())),
    )


def coverage_json(report: CoverageReport, run: ManifestView | None) -> str:
    """Render the report as one canonical-JSON line (plus newline)."""
    payload = {
        "schema_version": report.schema_version,
        "contract": SPAN_CONTRACT_VERSION,
        "run": None
        if run is None
        else {
            "run_id": run.run_id,
            "scenario_name": run.scenario_name,
            "scenario_version": run.scenario_version,
            "seed": run.seed,
            "session_count": run.session_count,
            "provider": run.provider,
            "model_id": run.model_id,
            "genai_semconv": run.genai_semconv,
        },
        "mapped_fraction": mapped_fraction(report),
        "layer_a": {
            "event_count": report.event_count,
            "census_present": report.census_present,
            "census_total": report.census_total,
            "per_event_type": {
                name: {"present": present, "total": total}
                for name, present, total in report.per_event_type
            },
        },
        "layer_b": {
            "span_count": report.layer_b_span_count,
            "expected_present": report.expected_present,
            "expected_total": report.expected_total,
            "per_kind": {
                name: {"present": present, "total": total}
                for name, present, total in report.per_kind
            },
        },
        "missing_keys": dict(report.missing_keys),
        "recorded_keys": dict(report.recorded_keys),
        "unknown_keys": dict(report.unknown_keys),
    }
    return canonical_json(payload) + "\n"

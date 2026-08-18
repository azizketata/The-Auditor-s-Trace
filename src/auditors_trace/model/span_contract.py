"""The ``at.*`` governance span contract, version ``at-span/1``.

This module is the one authoritative definition of the vocabulary that turns an
OpenTelemetry span into audit evidence: which attributes carry OCEL object
declarations, qualified event-to-object relations, O2O relations, and the
deterministic event ordering key. It is written in ``model/`` (stdlib only, no
third-party imports) so that both the writer (``scenario/emit.py``, needs the
``scenario`` extra) and the reader (``ingest/otel_reader.py`` and
``ingest/attribute_map.py``, core only) depend on it and neither depends on the
other.

Why a project-defined namespace exists at all: neither the OTel ``gen_ai.*``
conventions nor ``openinference.*`` has any concept of
``DataResource.classification``, ``lawful_basis``, ``PolicyVersion``,
``approver_role``, ``reason_codes``, or handoff from/to. The ``at.*`` namespace
is the minimal governance extension required to make an agent trace
reconstructable under AI Act Art. 12.

Layering rule: a span is an OCEL event **iff** it carries ``at.event.type``
(layer A, written by ``scenario/emit.py``). Spans emitted by the OpenInference
instrumentor (layer B: ``llm.*``, ``tool.*``, ``gen_ai.*``) never carry ``at.*``
keys and are enrichment only, reached by walking down from a layer-A span.

Encoding rules the reader can rely on:

- Object entries are sorted by ``(qualifier or "", object_type, object_id)``
  and O2O entries by ``(qualifier, source, target)`` before indices are
  assigned, so the encoding is independent of writer call order.
- A present-but-partial encoding (index gap, count mismatch, unknown qualifier,
  a declaration with no attributes) raises :class:`SpanContractError` — never a
  silent drop. ``parse_event`` returns ``None`` only when ``at.event.type`` is
  absent entirely.
- Values are omitted when ``None``; an empty string means a genuinely empty
  value, so "lawful_basis missing" and "lawful_basis empty" stay
  distinguishable for template T5.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier

SPAN_CONTRACT_VERSION: Final[str] = "at-span/1"

AttrValue = str | bool | int | float | tuple[str, ...]

# --- attribute keys -----------------------------------------------------------

CONTRACT: Final[str] = "at.contract"
SPAN_ROLE: Final[str] = "at.span.role"
SCENARIO_NAME: Final[str] = "at.scenario.name"
SCENARIO_VERSION: Final[str] = "at.scenario.version"
RUN_ID: Final[str] = "at.run.id"
RUN_SEED: Final[str] = "at.run.seed"
SESSION_ID: Final[str] = "at.session.id"
SESSION_WORKFLOW: Final[str] = "at.session.workflow"
APPLICATION_ID: Final[str] = "at.application.id"
APPLICANT_ID: Final[str] = "at.applicant.id"

EVENT_TYPE: Final[str] = "at.event.type"
EVENT_ID: Final[str] = "at.event.id"
EVENT_SEQ: Final[str] = "at.event.seq"
EVENT_TIME: Final[str] = "at.event.time"
EVENT_ACTOR_ID: Final[str] = "at.event.actor.id"
EVENT_ACTOR_TYPE: Final[str] = "at.event.actor.type"
EVENT_ATTR_PREFIX: Final[str] = "at.event.attr."

OBJ_COUNT: Final[str] = "at.obj.count"
O2O_COUNT: Final[str] = "at.o2o.count"

ROLE_SESSION_ROOT: Final[str] = "session_root"
ROLE_EVENT: Final[str] = "event"

# The one OpenInference key layer A also sets (so gen_ai.conversation.id stays
# consistent across both layers), plus the span-kind key.
OPENINFERENCE_SESSION_ID: Final[str] = "session.id"
OPENINFERENCE_SPAN_KIND: Final[str] = "openinference.span.kind"

E2O_QUALIFIERS: Final[frozenset[Qualifier]] = frozenset(
    {
        Qualifier.PRODUCES,
        Qualifier.CONCERNS,
        Qualifier.GOVERNED_BY,
        Qualifier.PERFORMED_BY,
        Qualifier.READS,
        Qualifier.APPROVES,
        Qualifier.FROM,
        Qualifier.TO,
        Qualifier.WITHIN,
        Qualifier.USES,
        Qualifier.REQUESTS,
        Qualifier.EXPLAINS,
    }
)
O2O_QUALIFIERS: Final[frozenset[Qualifier]] = frozenset(
    {Qualifier.DELEGATES_TO, Qualifier.DERIVED_FROM, Qualifier.SUBMITTED_BY}
)

ACTOR_TYPES: Final[frozenset[ObjectType]] = frozenset({ObjectType.AGENT, ObjectType.HUMAN_APPROVER})

#: Per-event-type actor policy, enforced at write time. ``None`` means the
#: event must carry no actor: session bounds are not agent actions, and T3's
#: clean-split safety depends on that being a contract rule rather than a
#: convention in one fleet's code. Approval verdicts are human acts.
ACTOR_POLICY: Final[Mapping[EventType, frozenset[ObjectType] | None]] = {
    EventType.SESSION_START: None,
    EventType.SESSION_END: None,
    EventType.INVOKE_AGENT: frozenset({ObjectType.AGENT}),
    EventType.HANDOFF: frozenset({ObjectType.AGENT}),
    EventType.CALL_LLM: frozenset({ObjectType.AGENT}),
    EventType.CALL_TOOL: frozenset({ObjectType.AGENT}),
    EventType.RETRIEVE_DATA: frozenset({ObjectType.AGENT}),
    EventType.EMIT_REASONING: frozenset({ObjectType.AGENT}),
    EventType.REQUEST_APPROVAL: frozenset({ObjectType.AGENT}),
    EventType.GRANT_APPROVAL: frozenset({ObjectType.HUMAN_APPROVER}),
    EventType.DENY_APPROVAL: frozenset({ObjectType.HUMAN_APPROVER}),
    EventType.MAKE_DECISION: frozenset({ObjectType.AGENT}),
    EventType.NOTIFY_APPLICANT: frozenset({ObjectType.AGENT}),
}

# Governance-relevant keys from the *standard* vocabularies that Phase 3 must
# also consume from layer-B spans. Vendored as literals: the semconv constants
# in opentelemetry-semantic-conventions 0.65b0 all live under `_incubating` and
# carry deprecation decorators that emit warnings.
STANDARD_GOVERNANCE_KEYS: Final[tuple[str, ...]] = (
    "gen_ai.conversation.id",
    "gen_ai.request.model",
    "gen_ai.tool.name",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "llm.invocation_parameters",
    "llm.model_name",
    "llm.provider",
    "llm.token_count.completion",
    "llm.token_count.prompt",
    "llm.token_count.total",
    "metadata",
    "openinference.span.kind",
    "session.id",
    "tool.description",
    "tool.name",
)

# Required at.event.attr.* names per event type (contract section F). The
# writer must supply these; the Phase 3 coverage report scores against them so
# the >0.9 gate has a fixed denominator defined here rather than inferred from
# whatever the scenario happened to emit.
REQUIRED_EVENT_ATTRS: Final[Mapping[EventType, tuple[str, ...]]] = {
    EventType.SESSION_START: ("contract", "run_id", "scenario_name", "scenario_version", "seed"),
    EventType.INVOKE_AGENT: ("agent_role", "step_index"),
    EventType.HANDOFF: ("handoff_kind", "reason"),
    EventType.CALL_LLM: ("max_tokens", "model_seed", "purpose", "temperature"),
    EventType.CALL_TOOL: ("status", "tool_call_id"),
    EventType.RETRIEVE_DATA: ("purpose", "record_count", "resource_type"),
    EventType.EMIT_REASONING: ("reasoning_kind", "text_hash"),
    EventType.REQUEST_APPROVAL: (
        "amount_dm",
        "proposed_outcome",
        "proposed_score",
        "required_role",
        "sla_seconds",
    ),
    EventType.GRANT_APPROVAL: ("approver_role", "authority_level", "rationale_hash"),
    EventType.DENY_APPROVAL: (
        "approver_role",
        "authority_level",
        "override_reason",
        "rationale_hash",
    ),
    EventType.MAKE_DECISION: ("adverse", "outcome", "reason_codes", "score"),
    EventType.NOTIFY_APPLICANT: ("adverse_action_notice", "channel", "contains_reason_codes"),
    EventType.SESSION_END: ("duration_seconds", "event_count", "outcome"),
}

_OBJ_FIELD_RE: Final[re.Pattern[str]] = re.compile(
    r"^at\.obj\.(\d+)\.(type|id|qualifier|declares)$"
)
_OBJ_ATTR_RE: Final[re.Pattern[str]] = re.compile(r"^at\.obj\.(\d+)\.attr\.(.+)$")
_O2O_FIELD_RE: Final[re.Pattern[str]] = re.compile(r"^at\.o2o\.(\d+)\.(source|target|qualifier)$")


class SpanContractError(ValueError):
    """A span violates the ``at.*`` contract. Raised, never silently dropped."""


def obj_key(i: int, leaf: str) -> str:
    """Return ``at.obj.{i}.{leaf}``."""
    return f"at.obj.{i}.{leaf}"


def obj_attr_key(i: int, name: str) -> str:
    """Return ``at.obj.{i}.attr.{name}``."""
    return f"at.obj.{i}.attr.{name}"


def o2o_key(i: int, leaf: str) -> str:
    """Return ``at.o2o.{i}.{leaf}``."""
    return f"at.o2o.{i}.{leaf}"


def _canonical_attrs(
    pairs: Sequence[tuple[str, AttrValue]],
) -> tuple[tuple[str, AttrValue], ...]:
    """Sort attribute pairs by name and normalise sequence values to tuples.

    Rejects at construction time everything that would otherwise be dropped or
    coerced *silently* downstream: empty names (their flattened keys don't
    parse), duplicate names (a dict encoding keeps only one), and value types
    OTel's attribute cleaner discards without incrementing any dropped counter
    (``None``, dicts, sets, bytes, ...).
    """
    out: list[tuple[str, AttrValue]] = []
    seen: set[str] = set()
    for name, value in pairs:
        if not name or not name.strip():
            raise SpanContractError("attribute names must be non-empty")
        if name in seen:
            raise SpanContractError(f"duplicate attribute name {name!r}")
        seen.add(name)
        if isinstance(value, (list, tuple)):
            out.append((name, tuple(str(v) for v in value)))
        elif isinstance(value, (str, bool, int, float)):
            out.append((name, value))
        else:
            raise SpanContractError(
                f"attribute {name!r}: value type {type(value).__name__} would be "
                "silently dropped by the OTel SDK; only str/bool/int/float/sequence-of-str "
                "are representable"
            )
    return tuple(sorted(out, key=lambda p: p[0]))


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """One event-to-object entry: a qualified relation, a declaration, or both.

    ``qualifier=None`` means a pure declaration contributing an object with no
    E2O relation (how the superseded PolicyVersion enters the log).
    ``declares=True`` means ``attributes`` is the object's complete attribute
    set; declarations happen exactly once per object id per trace.
    """

    object_type: ObjectType
    object_id: str
    qualifier: Qualifier | None = None
    declares: bool = False
    attributes: tuple[tuple[str, AttrValue], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", _canonical_attrs(self.attributes))


@dataclass(frozen=True, slots=True)
class O2ORef:
    """One object-to-object relation."""

    source_id: str
    target_id: str
    qualifier: Qualifier


@dataclass(frozen=True, slots=True)
class SessionScope:
    """The session-scope attributes present on every layer-A span in a trace."""

    scenario_name: str
    scenario_version: str
    run_id: str
    run_seed: int
    session_id: str
    workflow_name: str
    application_id: str
    applicant_id: str
    contract: str = SPAN_CONTRACT_VERSION


def _obj_sort_key(ref: ObjectRef) -> tuple[str, str, str]:
    return (ref.qualifier.value if ref.qualifier else "", ref.object_type.value, ref.object_id)


def _o2o_sort_key(ref: O2ORef) -> tuple[str, str, str]:
    return (ref.qualifier.value, ref.source_id, ref.target_id)


@dataclass(frozen=True, slots=True)
class EventSpec:
    """One OCEL event as carried by a layer-A span.

    Construction canonicalises ordering (objects, O2O relations, and attribute
    pairs are sorted), so ``parse_event(flatten_event(spec)) == spec`` holds
    exactly.
    """

    event_type: EventType
    event_id: str
    seq: int
    time: str  # ISO-8601 UTC with 'Z', from the simulated clock
    actor_id: str | None = None
    actor_type: ObjectType | None = None
    attributes: tuple[tuple[str, AttrValue], ...] = ()
    objects: tuple[ObjectRef, ...] = ()
    o2o: tuple[O2ORef, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", _canonical_attrs(self.attributes))
        object.__setattr__(self, "objects", tuple(sorted(self.objects, key=_obj_sort_key)))
        object.__setattr__(self, "o2o", tuple(sorted(self.o2o, key=_o2o_sort_key)))


def flatten_scope(scope: SessionScope) -> dict[str, AttrValue]:
    """Encode a session scope as span attributes."""
    return {
        CONTRACT: scope.contract,
        SCENARIO_NAME: scope.scenario_name,
        SCENARIO_VERSION: scope.scenario_version,
        RUN_ID: scope.run_id,
        RUN_SEED: scope.run_seed,
        SESSION_ID: scope.session_id,
        SESSION_WORKFLOW: scope.workflow_name,
        APPLICATION_ID: scope.application_id,
        APPLICANT_ID: scope.applicant_id,
    }


def flatten_event(spec: EventSpec) -> dict[str, AttrValue]:
    """Encode an event as span attributes. Deterministic and order-independent."""
    attrs: dict[str, AttrValue] = {
        EVENT_TYPE: spec.event_type.value,
        EVENT_ID: spec.event_id,
        EVENT_SEQ: spec.seq,
        EVENT_TIME: spec.time,
    }
    if spec.actor_id is not None:
        attrs[EVENT_ACTOR_ID] = spec.actor_id
    if spec.actor_type is not None:
        attrs[EVENT_ACTOR_TYPE] = spec.actor_type.value
    for name, value in spec.attributes:
        attrs[EVENT_ATTR_PREFIX + name] = value

    attrs[OBJ_COUNT] = len(spec.objects)
    for i, ref in enumerate(spec.objects):
        attrs[obj_key(i, "type")] = ref.object_type.value
        attrs[obj_key(i, "id")] = ref.object_id
        if ref.qualifier is not None:
            attrs[obj_key(i, "qualifier")] = ref.qualifier.value
        if ref.declares:
            attrs[obj_key(i, "declares")] = True
            for name, value in ref.attributes:
                attrs[obj_attr_key(i, name)] = value

    attrs[O2O_COUNT] = len(spec.o2o)
    for i, rel in enumerate(spec.o2o):
        attrs[o2o_key(i, "source")] = rel.source_id
        attrs[o2o_key(i, "target")] = rel.target_id
        attrs[o2o_key(i, "qualifier")] = rel.qualifier.value

    return attrs


def _as_attr_value(value: object, key: str) -> AttrValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise SpanContractError(f"{key}: unsupported attribute value type {type(value).__name__}")


def _require_str(attrs: Mapping[str, object], key: str) -> str:
    value = attrs.get(key)
    if not isinstance(value, str) or not value:
        raise SpanContractError(f"{key}: missing or not a non-empty string")
    return value


def _require_int(attrs: Mapping[str, object], key: str) -> int:
    value = attrs.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpanContractError(f"{key}: missing or not an integer")
    return value


def parse_scope(attrs: Mapping[str, object]) -> SessionScope | None:
    """Decode the session scope, or ``None`` when ``at.contract`` is absent.

    An unknown contract version is a hard error: the reader must never guess
    at a vocabulary it does not know.
    """
    contract = attrs.get(CONTRACT)
    if contract is None:
        return None
    if contract != SPAN_CONTRACT_VERSION:
        raise SpanContractError(
            f"unknown span contract version {contract!r}; this reader knows "
            f"{SPAN_CONTRACT_VERSION!r}"
        )
    return SessionScope(
        contract=SPAN_CONTRACT_VERSION,
        scenario_name=_require_str(attrs, SCENARIO_NAME),
        scenario_version=_require_str(attrs, SCENARIO_VERSION),
        run_id=_require_str(attrs, RUN_ID),
        run_seed=_require_int(attrs, RUN_SEED),
        session_id=_require_str(attrs, SESSION_ID),
        workflow_name=_require_str(attrs, SESSION_WORKFLOW),
        application_id=_require_str(attrs, APPLICATION_ID),
        applicant_id=_require_str(attrs, APPLICANT_ID),
    )


def _parse_enum_value(raw: object, enum: type, key: str) -> object:
    try:
        return enum(raw)
    except ValueError as exc:
        raise SpanContractError(f"{key}: unknown value {raw!r}") from exc


def _canonical_index(raw: str, key: str) -> int:
    """Reject non-canonical index spellings: ``at.obj.00.type`` must not
    silently merge into index 0 (a corrupted foreign encoding would otherwise
    be read as clean evidence, last-key-wins)."""
    if raw != str(int(raw)):
        raise SpanContractError(f"{key}: non-canonical index {raw!r}")
    return int(raw)


def _parse_objects(attrs: Mapping[str, object]) -> tuple[ObjectRef, ...]:
    count = _require_int(attrs, OBJ_COUNT)
    fields: dict[int, dict[str, object]] = {}
    obj_attrs: dict[int, list[tuple[str, AttrValue]]] = {}
    for key, value in attrs.items():
        m = _OBJ_FIELD_RE.match(key)
        if m:
            bucket = fields.setdefault(_canonical_index(m.group(1), key), {})
            if m.group(2) in bucket:
                raise SpanContractError(f"{key}: duplicate object field")
            bucket[m.group(2)] = value
            continue
        m = _OBJ_ATTR_RE.match(key)
        if m:
            obj_attrs.setdefault(_canonical_index(m.group(1), key), []).append(
                (m.group(2), _as_attr_value(value, key))
            )

    seen_indices = set(fields) | set(obj_attrs)
    if seen_indices and (min(seen_indices) < 0 or max(seen_indices) >= count):
        raise SpanContractError(
            f"{OBJ_COUNT}={count} but object indices {sorted(seen_indices)} present"
        )
    refs: list[ObjectRef] = []
    for i in range(count):
        entry = fields.get(i)
        if entry is None:
            raise SpanContractError(f"{OBJ_COUNT}={count} but index {i} is missing")
        obj_type = _parse_enum_value(entry.get("type"), ObjectType, obj_key(i, "type"))
        assert isinstance(obj_type, ObjectType)
        obj_id = entry.get("id")
        if not isinstance(obj_id, str) or not obj_id:
            raise SpanContractError(f"{obj_key(i, 'id')}: missing or empty")
        qualifier: Qualifier | None = None
        if "qualifier" in entry:
            q = _parse_enum_value(entry["qualifier"], Qualifier, obj_key(i, "qualifier"))
            assert isinstance(q, Qualifier)
            if q not in E2O_QUALIFIERS:
                raise SpanContractError(
                    f"{obj_key(i, 'qualifier')}: {q.value!r} is an O2O qualifier"
                )
            qualifier = q
        declares = entry.get("declares", False)
        if not isinstance(declares, bool):
            raise SpanContractError(f"{obj_key(i, 'declares')}: not a bool")
        attributes = tuple(obj_attrs.get(i, ()))
        if declares and not attributes:
            raise SpanContractError(
                f"{obj_key(i, 'declares')}: declaration of {obj_id!r} carries no attributes"
            )
        if not declares and attributes:
            raise SpanContractError(
                f"object {obj_id!r} at index {i} carries attributes without declares=true"
            )
        refs.append(
            ObjectRef(
                object_type=obj_type,
                object_id=obj_id,
                qualifier=qualifier,
                declares=declares,
                attributes=attributes,
            )
        )
    return tuple(refs)


def _parse_o2o(attrs: Mapping[str, object]) -> tuple[O2ORef, ...]:
    count = _require_int(attrs, O2O_COUNT)
    fields: dict[int, dict[str, object]] = {}
    for key, value in attrs.items():
        m = _O2O_FIELD_RE.match(key)
        if m:
            bucket = fields.setdefault(_canonical_index(m.group(1), key), {})
            if m.group(2) in bucket:
                raise SpanContractError(f"{key}: duplicate o2o field")
            bucket[m.group(2)] = value
    if fields and (min(fields) < 0 or max(fields) >= count):
        raise SpanContractError(f"{O2O_COUNT}={count} but o2o indices {sorted(fields)} present")
    rels: list[O2ORef] = []
    for i in range(count):
        entry = fields.get(i)
        if entry is None:
            raise SpanContractError(f"{O2O_COUNT}={count} but o2o index {i} is missing")
        source = entry.get("source")
        target = entry.get("target")
        if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
            raise SpanContractError(f"at.o2o.{i}: source/target missing or empty")
        q = _parse_enum_value(entry.get("qualifier"), Qualifier, o2o_key(i, "qualifier"))
        assert isinstance(q, Qualifier)
        if q not in O2O_QUALIFIERS:
            raise SpanContractError(f"{o2o_key(i, 'qualifier')}: {q.value!r} is not O2O")
        rels.append(O2ORef(source_id=source, target_id=target, qualifier=q))
    return tuple(rels)


def parse_event(attrs: Mapping[str, object]) -> EventSpec | None:
    """Decode an event, or ``None`` when ``at.event.type`` is absent entirely.

    A present-but-malformed encoding raises :class:`SpanContractError`.
    """
    raw_type = attrs.get(EVENT_TYPE)
    if raw_type is None:
        return None
    event_type = _parse_enum_value(raw_type, EventType, EVENT_TYPE)
    assert isinstance(event_type, EventType)

    actor_id: str | None = None
    actor_type: ObjectType | None = None
    if EVENT_ACTOR_ID in attrs:
        actor_id = _require_str(attrs, EVENT_ACTOR_ID)
    if EVENT_ACTOR_TYPE in attrs:
        at = _parse_enum_value(attrs[EVENT_ACTOR_TYPE], ObjectType, EVENT_ACTOR_TYPE)
        assert isinstance(at, ObjectType)
        actor_type = at

    event_attrs: list[tuple[str, AttrValue]] = []
    for key, value in attrs.items():
        if key.startswith(EVENT_ATTR_PREFIX):
            name = key[len(EVENT_ATTR_PREFIX) :]
            if not name:
                raise SpanContractError(f"{key}: empty event attribute name")
            event_attrs.append((name, _as_attr_value(value, key)))

    return EventSpec(
        event_type=event_type,
        event_id=_require_str(attrs, EVENT_ID),
        seq=_require_int(attrs, EVENT_SEQ),
        time=_require_str(attrs, EVENT_TIME),
        actor_id=actor_id,
        actor_type=actor_type,
        attributes=tuple(event_attrs),
        objects=_parse_objects(attrs),
        o2o=_parse_o2o(attrs),
    )


def validate_event(spec: EventSpec) -> None:
    """Enforce the contract on a spec before it is written to a span.

    Checks everything the encoder cannot express by construction: dense
    positive ``seq``, parseable timestamp, actor consistency, required event
    attributes for the event type, unique object ids within the event, and
    qualifier sidedness.
    """
    if spec.seq < 1:
        raise SpanContractError(f"{spec.event_id}: seq must be >= 1, got {spec.seq}")
    if not spec.event_id:
        raise SpanContractError("event_id must be non-empty")
    try:
        parsed = datetime.fromisoformat(spec.time)
    except ValueError as exc:
        raise SpanContractError(f"{spec.event_id}: time {spec.time!r} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise SpanContractError(f"{spec.event_id}: time {spec.time!r} carries no timezone")

    if (spec.actor_id is None) != (spec.actor_type is None):
        raise SpanContractError(f"{spec.event_id}: actor id and type must be set together")
    policy = ACTOR_POLICY[spec.event_type]
    if policy is None:
        if spec.actor_id is not None:
            raise SpanContractError(
                f"{spec.event_id}: {spec.event_type.value} must carry no actor — "
                "session bounds are not agent actions (T3 clean-split safety)"
            )
    else:
        if spec.actor_type is None:
            raise SpanContractError(
                f"{spec.event_id}: {spec.event_type.value} requires an actor of type "
                f"{sorted(t.value for t in policy)}"
            )
        if spec.actor_type not in policy:
            raise SpanContractError(
                f"{spec.event_id}: {spec.event_type.value} actor must be "
                f"{sorted(t.value for t in policy)}, got {spec.actor_type.value!r}"
            )

    provided = {name for name, _ in spec.attributes}
    missing = [a for a in REQUIRED_EVENT_ATTRS[spec.event_type] if a not in provided]
    if missing:
        raise SpanContractError(
            f"{spec.event_id}: {spec.event_type.value} requires event attrs {missing}"
        )

    attrs = dict(spec.attributes)
    if spec.event_type is EventType.MAKE_DECISION and attrs.get("adverse") is True:
        codes = attrs.get("reason_codes")
        if not isinstance(codes, tuple) or not codes:
            raise SpanContractError(
                f"{spec.event_id}: an adverse decision must carry non-empty reason_codes "
                "(T4 semantics are contract rules, not fleet conventions)"
            )
    if (
        spec.event_type is EventType.NOTIFY_APPLICANT
        and attrs.get("adverse_action_notice") is True
        and attrs.get("contains_reason_codes") is not True
    ):
        raise SpanContractError(
            f"{spec.event_id}: an adverse action notice must contain reason codes"
        )

    seen_ids: set[str] = set()
    for ref in spec.objects:
        if ref.object_id in seen_ids:
            raise SpanContractError(
                f"{spec.event_id}: object {ref.object_id!r} appears twice in one event"
            )
        seen_ids.add(ref.object_id)
        if ref.qualifier is not None and ref.qualifier not in E2O_QUALIFIERS:
            raise SpanContractError(
                f"{spec.event_id}: {ref.qualifier.value!r} is not an E2O qualifier"
            )
        if ref.qualifier is None and not ref.declares:
            raise SpanContractError(
                f"{spec.event_id}: object {ref.object_id!r} has neither a qualifier nor a "
                "declaration — the entry carries no information"
            )
        if ref.declares and not ref.attributes:
            raise SpanContractError(
                f"{spec.event_id}: declaration of {ref.object_id!r} carries no attributes"
            )
        if not ref.declares and ref.attributes:
            raise SpanContractError(
                f"{spec.event_id}: object {ref.object_id!r} carries attributes without "
                "declares=true — flatten_event would silently drop them"
            )
    for rel in spec.o2o:
        if rel.qualifier not in O2O_QUALIFIERS:
            raise SpanContractError(
                f"{spec.event_id}: {rel.qualifier.value!r} is not an O2O qualifier"
            )


def governance_census(event_type: EventType) -> tuple[str, ...]:
    """Return the fully-qualified attribute keys required on an event span.

    This is the fixed denominator for Phase 3's attribute-coverage report
    (claim C2): the envelope keys plus the event type's required
    ``at.event.attr.*`` names, sorted.
    """
    keys = [EVENT_TYPE, EVENT_ID, EVENT_SEQ, EVENT_TIME, OBJ_COUNT, O2O_COUNT]
    keys.extend(EVENT_ATTR_PREFIX + name for name in REQUIRED_EVENT_ATTRS[event_type])
    return tuple(sorted(keys))

"""OCEL 2.0 object types, event types, and relation qualifiers.

The three enums below are a pure transcription of BUILD-PLAN.md section 5 and
are shared by the span writer (``scenario/``) and the span reader (``ingest/``)
so the two vocabularies cannot drift. Enum *values* are the exact strings that
appear in span attributes and in the OCEL log: object types are PascalCase per
section 5's table; event types and qualifiers are snake_case.

The qualifier vocabulary adds exactly three entries beyond section 5's example
list — ``uses``, ``requests``, ``explains`` — because section 5 calls its list
"examples the mapper must produce" and template T4 explicitly needs an
``emit_reasoning`` event that references a decision. The set is frozen at
twelve E2O plus three O2O qualifiers.

The attribute-kind registry and the qualifier matrix below are the semantic
authority the Phase 1 OCEL model validates against. Both are transcriptions
of frozen reality: the scenario's emitted vocabulary (asserted against the
golden span files in ``tests/unit/test_ocel_schema.py``) with section 5's key
attributes as the required subset. Timestamps other than the event timestamp
(``submitted_at``, ``decided_at``, ``effective_*``) are deliberately STRING —
one temporal column keeps pm4py's date parsing out of attribute values.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping


class ObjectType(StrEnum):
    """The twelve OCEL object types of section 5."""

    APPLICATION = "Application"
    APPLICANT = "Applicant"
    AGENT = "Agent"
    CREDIT_DECISION = "CreditDecision"
    APPROVAL = "Approval"
    HUMAN_APPROVER = "HumanApprover"
    PROMPT = "Prompt"
    MODEL = "Model"
    TOOL = "Tool"
    DATA_RESOURCE = "DataResource"
    POLICY_VERSION = "PolicyVersion"
    SESSION = "Session"


class EventType(StrEnum):
    """The thirteen OCEL event types of section 5."""

    SESSION_START = "session_start"
    INVOKE_AGENT = "invoke_agent"
    HANDOFF = "handoff"
    CALL_LLM = "call_llm"
    CALL_TOOL = "call_tool"
    RETRIEVE_DATA = "retrieve_data"
    EMIT_REASONING = "emit_reasoning"
    REQUEST_APPROVAL = "request_approval"
    GRANT_APPROVAL = "grant_approval"
    DENY_APPROVAL = "deny_approval"
    MAKE_DECISION = "make_decision"
    NOTIFY_APPLICANT = "notify_applicant"
    SESSION_END = "session_end"


class Qualifier(StrEnum):
    """Event-to-object and object-to-object relation qualifiers.

    The first twelve are span-level E2O; the last three are O2O. The split is
    enforced by ``model.span_contract`` at write and parse time.

    ``DECLARES`` is OCEL-level only: it never appears in ``at.*`` spans (a
    pure declaration is a qualifier-less entry there). The Phase 3 mapper
    synthesizes a ``declares`` E2O relation from the declaring event for every
    pure declaration, because pm4py's OCEL 2.0 exporters silently delete any
    object with zero E2O relations — and every O2O edge touching it (spike,
    19 Aug 2026: ``docs/SPIKE-pm4py-roundtrip.md``).
    """

    PRODUCES = "produces"
    CONCERNS = "concerns"
    GOVERNED_BY = "governed_by"
    PERFORMED_BY = "performed_by"
    READS = "reads"
    APPROVES = "approves"
    FROM = "from"
    TO = "to"
    WITHIN = "within"
    USES = "uses"
    REQUESTS = "requests"
    EXPLAINS = "explains"
    DELEGATES_TO = "delegates_to"
    DERIVED_FROM = "derived_from"
    SUBMITTED_BY = "submitted_by"
    DECLARES = "declares"


class AttributeKind(StrEnum):
    """The value kinds an OCEL attribute may carry.

    OCEL 2.0 attribute values are scalars; STRING_LIST is our one encoded
    kind — ``model/io.py`` serialises it as a canonical JSON array string
    (``reason_codes`` becomes ``'["RC01","RC07"]'``).
    """

    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    STRING_LIST = "string_list"


_S = AttributeKind.STRING
_B = AttributeKind.BOOLEAN
_I = AttributeKind.INTEGER
_F = AttributeKind.FLOAT
_L = AttributeKind.STRING_LIST

#: The frozen object-attribute vocabulary: exactly what the Phase 2 scenario
#: declares per object type (a superset of section 5's key attributes).
_OBJECT_ATTRS: Final[Mapping[ObjectType, Mapping[str, AttributeKind]]] = MappingProxyType(
    {
        ObjectType.APPLICATION: MappingProxyType(
            {
                "application_id": _S,
                "product_type": _S,
                "amount": _I,
                "currency": _S,
                "term_months": _I,
                "purpose": _S,
                "submitted_at": _S,
            }
        ),
        ObjectType.APPLICANT: MappingProxyType(
            {"applicant_id": _S, "jurisdiction": _S, "pseudonym_scheme": _S}
        ),
        ObjectType.AGENT: MappingProxyType(
            {"agent_id": _S, "name": _S, "role": _S, "version": _S, "framework": _S}
        ),
        ObjectType.CREDIT_DECISION: MappingProxyType(
            {
                "decision_id": _S,
                "outcome": _S,
                "score": _I,
                "reason_codes": _L,
                "band": _S,
                "decided_at": _S,
            }
        ),
        ObjectType.APPROVAL: MappingProxyType(
            {"approval_id": _S, "approver_role": _S, "granted": _B, "decided_at": _S}
        ),
        ObjectType.HUMAN_APPROVER: MappingProxyType(
            {"approver_id": _S, "role": _S, "authority_level": _I}
        ),
        ObjectType.PROMPT: MappingProxyType(
            {"prompt_name": _S, "prompt_version": _S, "template_hash": _S}
        ),
        ObjectType.MODEL: MappingProxyType({"model_id": _S, "provider": _S, "version": _S}),
        ObjectType.TOOL: MappingProxyType({"tool_name": _S, "tool_type": _S}),
        ObjectType.DATA_RESOURCE: MappingProxyType(
            {
                "resource_id": _S,
                "resource_type": _S,
                "classification": _S,
                "lawful_basis": _S,
                "controller": _S,
                "retention_class": _S,
            }
        ),
        ObjectType.POLICY_VERSION: MappingProxyType(
            {"policy_id": _S, "version": _S, "effective_from": _S, "effective_to": _S}
        ),
        ObjectType.SESSION: MappingProxyType({"session_id": _S, "workflow_name": _S}),
    }
)

#: The frozen event-attribute vocabulary: the span contract's required set
#: per event type plus the scenario's optional extras (``emit_reasoning``
#: carries ``reason_codes`` only after a decision exists).
_EVENT_ATTRS: Final[Mapping[EventType, Mapping[str, AttributeKind]]] = MappingProxyType(
    {
        EventType.SESSION_START: MappingProxyType(
            {"contract": _S, "run_id": _S, "scenario_name": _S, "scenario_version": _S, "seed": _I}
        ),
        EventType.INVOKE_AGENT: MappingProxyType({"agent_role": _S, "step_index": _I}),
        EventType.HANDOFF: MappingProxyType({"handoff_kind": _S, "reason": _S}),
        EventType.CALL_LLM: MappingProxyType(
            {"max_tokens": _I, "model_seed": _I, "purpose": _S, "temperature": _F}
        ),
        EventType.CALL_TOOL: MappingProxyType({"status": _S, "tool_call_id": _S}),
        EventType.RETRIEVE_DATA: MappingProxyType(
            {"purpose": _S, "record_count": _I, "resource_type": _S}
        ),
        EventType.EMIT_REASONING: MappingProxyType(
            {"reasoning_kind": _S, "text_hash": _S, "reason_codes": _L}
        ),
        EventType.REQUEST_APPROVAL: MappingProxyType(
            {
                "amount_dm": _I,
                "proposed_outcome": _S,
                "proposed_score": _I,
                "required_role": _S,
                "sla_seconds": _I,
            }
        ),
        EventType.GRANT_APPROVAL: MappingProxyType(
            {"approver_role": _S, "authority_level": _I, "rationale_hash": _S}
        ),
        EventType.DENY_APPROVAL: MappingProxyType(
            {
                "approver_role": _S,
                "authority_level": _I,
                "override_reason": _S,
                "rationale_hash": _S,
            }
        ),
        EventType.MAKE_DECISION: MappingProxyType(
            {"adverse": _B, "outcome": _S, "reason_codes": _L, "score": _I}
        ),
        EventType.NOTIFY_APPLICANT: MappingProxyType(
            {"adverse_action_notice": _B, "channel": _S, "contains_reason_codes": _B}
        ),
        EventType.SESSION_END: MappingProxyType(
            {"duration_seconds": _I, "event_count": _I, "outcome": _S}
        ),
    }
)

#: Where each object type is declared — the event types whose spans carry the
#: object's ``declares`` entry. The ``declares`` OCEL qualifier is legal
#: exactly at these pairs (spike constraint G1).
_DECLARING_EVENTS: Final[Mapping[ObjectType, frozenset[EventType]]] = MappingProxyType(
    {
        ObjectType.SESSION: frozenset({EventType.SESSION_START}),
        ObjectType.APPLICATION: frozenset({EventType.SESSION_START}),
        ObjectType.APPLICANT: frozenset({EventType.SESSION_START}),
        ObjectType.POLICY_VERSION: frozenset({EventType.SESSION_START}),
        ObjectType.AGENT: frozenset({EventType.INVOKE_AGENT}),
        ObjectType.MODEL: frozenset({EventType.CALL_LLM}),
        ObjectType.PROMPT: frozenset({EventType.CALL_LLM}),
        ObjectType.TOOL: frozenset({EventType.CALL_TOOL}),
        ObjectType.DATA_RESOURCE: frozenset({EventType.RETRIEVE_DATA}),
        ObjectType.HUMAN_APPROVER: frozenset({EventType.REQUEST_APPROVAL}),
        ObjectType.APPROVAL: frozenset({EventType.GRANT_APPROVAL, EventType.DENY_APPROVAL}),
        ObjectType.CREDIT_DECISION: frozenset({EventType.MAKE_DECISION}),
    }
)

#: The semantic qualifier matrix: every (event, object, qualifier) triple the
#: frozen scenario emits, transcribed from the golden span files (asserted
#: 1:1 in tests/unit/test_ocel_schema.py). ``declares`` is injected below at
#: every declaring pair rather than listed here.
_SEMANTIC_MATRIX: Final[dict[tuple[EventType, ObjectType], frozenset[Qualifier]]] = {
    (EventType.SESSION_START, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.SESSION_START, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.SESSION_START, ObjectType.APPLICANT): frozenset({Qualifier.CONCERNS}),
    (EventType.SESSION_START, ObjectType.POLICY_VERSION): frozenset({Qualifier.GOVERNED_BY}),
    (EventType.INVOKE_AGENT, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.INVOKE_AGENT, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.INVOKE_AGENT, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.HANDOFF, ObjectType.AGENT): frozenset({Qualifier.FROM, Qualifier.TO}),
    (EventType.HANDOFF, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.HANDOFF, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.CALL_LLM, ObjectType.MODEL): frozenset({Qualifier.USES}),
    (EventType.CALL_LLM, ObjectType.PROMPT): frozenset({Qualifier.USES}),
    (EventType.CALL_LLM, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.CALL_LLM, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.CALL_LLM, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.CALL_TOOL, ObjectType.TOOL): frozenset({Qualifier.USES}),
    (EventType.CALL_TOOL, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.CALL_TOOL, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.CALL_TOOL, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.RETRIEVE_DATA, ObjectType.DATA_RESOURCE): frozenset({Qualifier.READS}),
    (EventType.RETRIEVE_DATA, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.RETRIEVE_DATA, ObjectType.APPLICANT): frozenset({Qualifier.CONCERNS}),
    (EventType.RETRIEVE_DATA, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.RETRIEVE_DATA, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.EMIT_REASONING, ObjectType.CREDIT_DECISION): frozenset({Qualifier.EXPLAINS}),
    (EventType.EMIT_REASONING, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.EMIT_REASONING, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.EMIT_REASONING, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.REQUEST_APPROVAL, ObjectType.APPROVAL): frozenset({Qualifier.REQUESTS}),
    (EventType.REQUEST_APPROVAL, ObjectType.HUMAN_APPROVER): frozenset({Qualifier.CONCERNS}),
    (EventType.REQUEST_APPROVAL, ObjectType.CREDIT_DECISION): frozenset({Qualifier.CONCERNS}),
    (EventType.REQUEST_APPROVAL, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.REQUEST_APPROVAL, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.REQUEST_APPROVAL, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.GRANT_APPROVAL, ObjectType.APPROVAL): frozenset({Qualifier.PRODUCES}),
    (EventType.GRANT_APPROVAL, ObjectType.HUMAN_APPROVER): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.GRANT_APPROVAL, ObjectType.CREDIT_DECISION): frozenset({Qualifier.APPROVES}),
    (EventType.GRANT_APPROVAL, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.GRANT_APPROVAL, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    # Section 5 reserves `approves` for grant_approval; a denial concerns the
    # decision it declines to endorse.
    (EventType.DENY_APPROVAL, ObjectType.APPROVAL): frozenset({Qualifier.PRODUCES}),
    (EventType.DENY_APPROVAL, ObjectType.HUMAN_APPROVER): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.DENY_APPROVAL, ObjectType.CREDIT_DECISION): frozenset({Qualifier.CONCERNS}),
    (EventType.DENY_APPROVAL, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.DENY_APPROVAL, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.MAKE_DECISION, ObjectType.CREDIT_DECISION): frozenset({Qualifier.PRODUCES}),
    (EventType.MAKE_DECISION, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.MAKE_DECISION, ObjectType.APPLICANT): frozenset({Qualifier.CONCERNS}),
    (EventType.MAKE_DECISION, ObjectType.POLICY_VERSION): frozenset({Qualifier.GOVERNED_BY}),
    (EventType.MAKE_DECISION, ObjectType.APPROVAL): frozenset({Qualifier.CONCERNS}),
    (EventType.MAKE_DECISION, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.MAKE_DECISION, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.NOTIFY_APPLICANT, ObjectType.APPLICANT): frozenset({Qualifier.CONCERNS}),
    (EventType.NOTIFY_APPLICANT, ObjectType.CREDIT_DECISION): frozenset({Qualifier.CONCERNS}),
    (EventType.NOTIFY_APPLICANT, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.NOTIFY_APPLICANT, ObjectType.AGENT): frozenset({Qualifier.PERFORMED_BY}),
    (EventType.NOTIFY_APPLICANT, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.SESSION_END, ObjectType.SESSION): frozenset({Qualifier.WITHIN}),
    (EventType.SESSION_END, ObjectType.APPLICATION): frozenset({Qualifier.CONCERNS}),
    (EventType.SESSION_END, ObjectType.CREDIT_DECISION): frozenset({Qualifier.CONCERNS}),
}

#: O2O qualifier -> (source type, target type), per section 5.
_O2O_ENDPOINTS: Final[Mapping[Qualifier, tuple[ObjectType, ObjectType]]] = MappingProxyType(
    {
        Qualifier.DELEGATES_TO: (ObjectType.AGENT, ObjectType.AGENT),
        Qualifier.DERIVED_FROM: (ObjectType.CREDIT_DECISION, ObjectType.APPLICATION),
        Qualifier.SUBMITTED_BY: (ObjectType.APPLICATION, ObjectType.APPLICANT),
    }
)


def _build_matrix() -> Mapping[tuple[EventType, ObjectType], tuple[Qualifier, ...]]:
    combined: dict[tuple[EventType, ObjectType], set[Qualifier]] = {
        pair: set(qualifiers) for pair, qualifiers in _SEMANTIC_MATRIX.items()
    }
    for object_type, declarers in _DECLARING_EVENTS.items():
        for event_type in declarers:
            combined.setdefault((event_type, object_type), set()).add(Qualifier.DECLARES)
    return MappingProxyType(
        {
            pair: tuple(sorted(qualifiers, key=lambda q: q.value))
            for pair, qualifiers in combined.items()
        }
    )


_ALLOWED: Final[Mapping[tuple[EventType, ObjectType], tuple[Qualifier, ...]]] = _build_matrix()


def object_type_attributes(object_type: ObjectType) -> tuple[str, ...]:
    """Return the declared attribute names for an object type, sorted."""
    return tuple(sorted(_OBJECT_ATTRS[object_type]))


def object_attribute_kinds(object_type: ObjectType) -> Mapping[str, AttributeKind]:
    """Return the attribute-name-to-kind registry for an object type."""
    return _OBJECT_ATTRS[object_type]


def event_attribute_kinds(event_type: EventType) -> Mapping[str, AttributeKind]:
    """Return the attribute-name-to-kind registry for an event type."""
    return _EVENT_ATTRS[event_type]


def declaring_events(object_type: ObjectType) -> frozenset[EventType]:
    """Return the event types at which an object type is declared."""
    return _DECLARING_EVENTS[object_type]


def o2o_endpoints(qualifier: Qualifier) -> tuple[ObjectType, ObjectType]:
    """Return the (source, target) object types an O2O qualifier connects."""
    try:
        return _O2O_ENDPOINTS[qualifier]
    except KeyError:
        raise ValueError(f"{qualifier.value!r} is not an o2o qualifier") from None


def allowed_qualifiers(event_type: EventType, object_type: ObjectType) -> tuple[Qualifier, ...]:
    """Return the qualifiers section 5 permits between an event and object type.

    Sorted by qualifier value; an unlisted pair returns the empty tuple.
    """
    return _ALLOWED.get((event_type, object_type), ())

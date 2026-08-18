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

``object_type_attributes`` and ``allowed_qualifiers`` remain Phase 1
deliverables.
"""

from __future__ import annotations

from enum import StrEnum


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

    The first twelve are E2O; the last three are O2O. The split is enforced by
    ``model.span_contract`` at write and parse time.
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


def object_type_attributes(object_type: ObjectType) -> tuple[str, ...]:
    """Return the declared attribute names for an object type, sorted."""
    raise NotImplementedError


def allowed_qualifiers(event_type: EventType, object_type: ObjectType) -> tuple[Qualifier, ...]:
    """Return the qualifiers section 5 permits between an event and object type."""
    raise NotImplementedError

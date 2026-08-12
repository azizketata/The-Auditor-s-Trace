"""OCEL 2.0 object types, event types, and relation qualifiers.

Phase 1 deliverable. Builds to BUILD-PLAN.md section 5 exactly: twelve object
types, thirteen event types, and the qualified event-to-object relations that
distinguish this model from flat XES.

The placeholder declarations below exist so that Phase 0 signatures typecheck.
Phase 1 replaces them with the real definitions.
"""

from __future__ import annotations

from enum import StrEnum


class ObjectType(StrEnum):
    """The twelve OCEL object types of section 5. Populated in Phase 1."""


class EventType(StrEnum):
    """The thirteen OCEL event types of section 5. Populated in Phase 1."""


class Qualifier(StrEnum):
    """Event-to-object relation qualifiers (`produces`, `concerns`, ...)."""


def object_type_attributes(object_type: ObjectType) -> tuple[str, ...]:
    """Return the declared attribute names for an object type, sorted."""
    raise NotImplementedError


def allowed_qualifiers(event_type: EventType, object_type: ObjectType) -> tuple[Qualifier, ...]:
    """Return the qualifiers section 5 permits between an event and object type."""
    raise NotImplementedError

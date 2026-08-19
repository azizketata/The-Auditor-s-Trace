"""In-memory OCEL 2.0 log.

Phase 1 deliverable. Holds sorted, immutable collections; every accessor
returns a deterministic order. Nothing here may consult wall-clock time, and
nothing here may import pandas or pm4py — serialisation lives in
``model/io.py``; this module is stdlib-only, like ``span_contract``.

Every validation rule R1-R15 exists because a serialisation layer or the
hash chain depends on it (docs/SPIKE-pm4py-roundtrip.md): pm4py's exporters
silently delete relation-less objects (R13/G1), its JSON importer
deduplicates event-object pairs (R7/G2), its SQLite importer corrupts
numeric-looking ids (R2), its JSON importer turns the literal string "null"
into None and its XML importer turns empty element text into the literal
string 'None' (R5 keeps both decodes bijective), and the canonical hash
needs exactly one spelling per value (R4, R6). Raised, never repaired.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

from auditors_trace.model.ocel_schema import (
    AttributeKind,
    EventType,
    ObjectType,
    Qualifier,
    allowed_qualifiers,
    event_attribute_kinds,
    o2o_endpoints,
    object_attribute_kinds,
)
from auditors_trace.model.span_contract import (
    E2O_QUALIFIERS,
    O2O_QUALIFIERS,
    AttrValue,
    O2ORef,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class OCELModelError(ValueError):
    """The in-memory OCEL model rejects invalid content. Raised, never repaired."""


#: The OCEL-level E2O qualifier set: the span-level set plus ``declares``,
#: which the Phase 3 mapper synthesizes for pure declarations (G1). Lives
#: here because ``span_contract`` is version-frozen at ``at-span/1``.
OCEL_E2O_QUALIFIERS: Final[frozenset[Qualifier]] = frozenset(E2O_QUALIFIERS | {Qualifier.DECLARES})

#: One canonical timestamp spelling — whole-second UTC with a literal Z —
#: matching the simulated clock (``scenario/clock.py``) byte for byte.
TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"

_NUMERIC_LITERAL: Final[re.Pattern[str]] = re.compile(r"-?\d+(\.\d+)?")


def canonical_json(obj: object) -> str:
    """The house canonical JSON (CLAUDE.md rule 2)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_id(owner: str, value: str) -> None:
    if not value:
        raise OCELModelError(f"{owner} id is empty")
    if _NUMERIC_LITERAL.fullmatch(value):
        raise OCELModelError(
            f"{owner} id {value!r} reads as a numeric literal; pm4py's sqlite "
            "importer strips trailing '.0' from such ids, so they are banned outright"
        )


def _validate_attrs(
    owner: str,
    attrs: tuple[tuple[str, AttrValue], ...],
    registry: Mapping[str, AttributeKind],
) -> tuple[tuple[str, AttrValue], ...]:
    pairs = tuple(attrs)
    names = [name for name, _ in pairs]
    for name in names:
        if not name:
            raise OCELModelError(f"{owner} has an attribute with an empty name")
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise OCELModelError(f"{owner} has duplicate attribute names: {sorted(duplicates)}")
    for name, value in pairs:
        if name not in registry:
            raise OCELModelError(
                f"{owner} carries unknown attribute {name!r}; the frozen vocabulary "
                f"allows {sorted(registry)}"
            )
        kind = registry[name]
        _validate_value(owner, name, kind, value)
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


def _validate_value(owner: str, name: str, kind: AttributeKind, value: AttrValue) -> None:
    def fail(got: str) -> OCELModelError:
        return OCELModelError(f"attribute {name!r} on {owner} must be {kind.value}, got {got}")

    if kind is AttributeKind.STRING:
        if not isinstance(value, str):
            raise fail(type(value).__name__)
        if value.strip().lower() in ("null", "none"):
            raise OCELModelError(
                f"attribute {name!r} on {owner} holds the unrepresentable string "
                f"{value!r}: pm4py's JSON importer maps 'null' to None and its XML "
                "importer renders empty text as 'None', so both spellings are banned "
                "to keep the decode bijective"
            )
    elif kind is AttributeKind.BOOLEAN:
        if not isinstance(value, bool):
            raise fail(type(value).__name__)
    elif kind is AttributeKind.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise fail(type(value).__name__)
    elif kind is AttributeKind.FLOAT:
        if isinstance(value, bool) or not isinstance(value, float):
            raise fail(type(value).__name__)
        if not math.isfinite(value):
            raise OCELModelError(
                f"attribute {name!r} on {owner} must be a finite float; non-finite "
                "values have no canonical JSON spelling"
            )
    elif kind is AttributeKind.STRING_LIST:
        if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
            raise fail(type(value).__name__)
    else:  # pragma: no cover - the enum is closed
        raise fail(type(value).__name__)


@dataclass(frozen=True, slots=True)
class OCELRelation:
    """One qualified E2O relation, owned by its event."""

    object_id: str
    qualifier: Qualifier

    def __post_init__(self) -> None:
        _validate_id("related object", self.object_id)
        if self.qualifier not in OCEL_E2O_QUALIFIERS:
            raise OCELModelError(
                f"{self.qualifier.value!r} is an o2o qualifier and cannot appear on "
                "an event-to-object relation"
            )


@dataclass(frozen=True, slots=True)
class OCELObject:
    """An OCEL object: id, type, and its sorted attribute pairs."""

    object_id: str
    object_type: ObjectType
    attributes: tuple[tuple[str, AttrValue], ...] = ()

    def __post_init__(self) -> None:
        _validate_id("object", self.object_id)
        canonical = _validate_attrs(
            f"object {self.object_id!r}",
            tuple(self.attributes),
            object_attribute_kinds(self.object_type),
        )
        object.__setattr__(self, "attributes", canonical)


@dataclass(frozen=True, slots=True)
class OCELEvent:
    """An OCEL event with its qualified object relations, sorted and validated."""

    event_id: str
    event_type: EventType
    timestamp: str
    attributes: tuple[tuple[str, AttrValue], ...] = ()
    relations: tuple[OCELRelation, ...] = ()

    def __post_init__(self) -> None:
        _validate_id("event", self.event_id)
        try:
            parsed = datetime.strptime(self.timestamp, TIMESTAMP_FORMAT)
        except ValueError:
            parsed = None
        if parsed is None or parsed.strftime(TIMESTAMP_FORMAT) != self.timestamp:
            raise OCELModelError(
                f"event {self.event_id!r} timestamp {self.timestamp!r} is not "
                "canonical whole-second UTC ('%Y-%m-%dT%H:%M:%SZ'); the hash needs "
                "one spelling and pm4py's JSON exporter truncates sub-seconds anyway"
            )
        canonical = _validate_attrs(
            f"event {self.event_id!r}",
            tuple(self.attributes),
            event_attribute_kinds(self.event_type),
        )
        object.__setattr__(self, "attributes", canonical)

        relations = tuple(self.relations)
        if not relations:
            raise OCELModelError(
                f"event {self.event_id!r} must carry at least one relation; an "
                "unanchored event survives no flattening"
            )
        related = [relation.object_id for relation in relations]
        if len(set(related)) != len(related):
            raise OCELModelError(
                f"event {self.event_id!r} may carry at most one relation per "
                "(event, object) pair: pm4py's JSON importer silently deduplicates "
                "them, keeping the last"
            )
        object.__setattr__(self, "relations", tuple(sorted(relations, key=lambda r: r.object_id)))


@dataclass(frozen=True, slots=True)
class OCELLog:
    """An OCEL 2.0 log: objects, events with qualified relations, O2O relations."""

    events: tuple[OCELEvent, ...] = ()
    objects: tuple[OCELObject, ...] = ()
    o2o: tuple[O2ORef, ...] = ()

    def __post_init__(self) -> None:
        events = tuple(sorted(self.events, key=lambda e: (e.timestamp, e.event_id)))
        objects = tuple(sorted(self.objects, key=lambda o: (o.object_type.value, o.object_id)))
        o2o = tuple(sorted(self.o2o, key=lambda r: (r.qualifier.value, r.source_id, r.target_id)))
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "objects", objects)
        object.__setattr__(self, "o2o", o2o)

        event_ids = [event.event_id for event in events]
        if len(set(event_ids)) != len(event_ids):
            raise OCELModelError("duplicate event id in log")
        object_ids = [obj.object_id for obj in objects]
        if len(set(object_ids)) != len(object_ids):
            raise OCELModelError("duplicate object id in log")

        types_by_id = {obj.object_id: obj.object_type for obj in objects}
        related: set[str] = set()
        for event in events:
            for relation in event.relations:
                object_type = types_by_id.get(relation.object_id)
                if object_type is None:
                    raise OCELModelError(
                        f"event {event.event_id!r} relates to undeclared object "
                        f"{relation.object_id!r}"
                    )
                if relation.qualifier not in allowed_qualifiers(event.event_type, object_type):
                    raise OCELModelError(
                        f"qualifier {relation.qualifier.value!r} is not permitted "
                        f"between {event.event_type.value!r} and {object_type.value!r}"
                    )
                related.add(relation.object_id)
        for obj in objects:
            if obj.object_id not in related:
                raise OCELModelError(
                    f"object {obj.object_id!r} has no E2O relation; pm4py's OCEL 2.0 "
                    "exporters silently delete such objects and every O2O edge "
                    "touching them (spike F1) — materialise a 'declares' relation "
                    "from its declaring event instead"
                )

        seen: set[tuple[str, str, str]] = set()
        for rel in o2o:
            if rel.qualifier not in O2O_QUALIFIERS:
                raise OCELModelError(f"{rel.qualifier.value!r} is not an o2o qualifier")
            triple = (rel.qualifier.value, rel.source_id, rel.target_id)
            if triple in seen:
                raise OCELModelError(f"duplicate o2o relation {triple}")
            seen.add(triple)
            source_type = types_by_id.get(rel.source_id)
            target_type = types_by_id.get(rel.target_id)
            if source_type is None or target_type is None:
                raise OCELModelError(
                    f"o2o relation {triple} cites an object the log does not declare"
                )
            expected = o2o_endpoints(rel.qualifier)
            if (source_type, target_type) != expected:
                raise OCELModelError(
                    f"o2o qualifier {rel.qualifier.value!r} must connect "
                    f"{expected[0].value} -> {expected[1].value}, got "
                    f"{source_type.value} -> {target_type.value}"
                )


def events_sorted(log: OCELLog) -> tuple[OCELEvent, ...]:
    """Return every event in deterministic order: (timestamp, event id)."""
    return log.events


def objects_sorted(log: OCELLog) -> tuple[OCELObject, ...]:
    """Return every object in deterministic order: (object type, object id)."""
    return log.objects


def log_hash(log: OCELLog) -> str:
    """Return the canonical sha256 of the log content, excluding file metadata.

    Stable across runs and across serialisation formats: computed over the
    canonical JSON of the in-memory model, never over file bytes (spike
    constraint G4). This is the ``input_log_sha256`` that every evidence
    record cites.
    """
    payload = {
        "events": [
            {
                "attributes": dict(event.attributes),
                "id": event.event_id,
                "relations": [
                    {"object": r.object_id, "qualifier": r.qualifier.value} for r in event.relations
                ],
                "time": event.timestamp,
                "type": event.event_type.value,
            }
            for event in log.events
        ],
        "o2o": [
            {"qualifier": r.qualifier.value, "source": r.source_id, "target": r.target_id}
            for r in log.o2o
        ],
        "objects": [
            {
                "attributes": dict(obj.attributes),
                "id": obj.object_id,
                "type": obj.object_type.value,
            }
            for obj in log.objects
        ],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

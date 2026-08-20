"""Object-centric directly-follows graph baseline.

Phase 7 deliverable. pm4py's OC-DFG as the non-declarative object-centric
comparison: discover the clean split's directly-follows edges per object
type, then flag every (object type, edge) in the evaluated log that the
clean reference never exhibits.

This is an anomaly detector, not a classifier: a novel edge has no
constraint-class semantics, so detections carry the synthetic id
``OCDFG.novel_edge`` and are scored via ``eval.metrics.session_match``
(localisation credit) only. Unlike DECLARE, the OC-DFG result structure
carries REAL event-id pairs per edge, so every detection cites exact events
for free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auditors_trace.constraints.templates import Violation, build_index, violation_sort_key
from auditors_trace.model.io import to_pm4py
from auditors_trace.model.log import OCELLog

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class OcdfgReference:
    """The clean split's directly-follows edges: (object type, from, to)."""

    edges: frozenset[tuple[str, str, str]]


def _edge_couples(log: OCELLog) -> Mapping[tuple[str, str, str], tuple[tuple[str, str], ...]]:
    """(otype, act_a, act_b) -> sorted real event-id couples, from pm4py."""
    import pm4py

    ocdfg = pm4py.discover_ocdfg(to_pm4py(log))
    couples: dict[tuple[str, str, str], tuple[tuple[str, str], ...]] = {}
    event_couples = ocdfg["edges"]["event_couples"]
    for object_type in sorted(event_couples):
        for (act_a, act_b), pairs in sorted(event_couples[object_type].items()):
            couples[(object_type, act_a, act_b)] = tuple(sorted(pairs))
    return couples


def discover_reference(clean_log: OCELLog) -> OcdfgReference:
    """Discover the clean reference edge set."""
    return OcdfgReference(edges=frozenset(_edge_couples(clean_log)))


def detect(log: OCELLog, *, reference: OcdfgReference) -> list[Violation]:
    """Flag every event couple on an edge the clean reference never shows.

    One Violation per real event-id pair, chronological within the pair,
    session attributed from the first event (fallback second), canonical
    output order.
    """
    index = build_index(log)
    violations: list[Violation] = []
    for (object_type, act_a, act_b), pairs in _edge_couples(log).items():
        if (object_type, act_a, act_b) in reference.edges:
            continue
        for first, second in pairs:
            session_id = index.session_of_event.get(first, index.session_of_event.get(second, ""))
            violations.append(
                Violation(
                    constraint_id="OCDFG.novel_edge",
                    ocel_event_ids=(first, second),
                    session_id=session_id,
                    detail=f"{object_type}: {act_a} -> {act_b}",
                )
            )
    return sorted(violations, key=violation_sort_key)

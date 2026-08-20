"""The OC-DFG baseline (Phase 7): novel-edge anomaly detection with real ids."""

from __future__ import annotations

import dataclasses

from auditors_trace.baselines.ocdfg import detect, discover_reference
from auditors_trace.constraints.templates import violation_sort_key
from auditors_trace.model.log import OCELLog


def _delete_approval(log: OCELLog) -> OCELLog:
    """Removes session A's approval events -> novel directly-follows edges."""
    kept_events = tuple(
        dataclasses.replace(
            event,
            relations=tuple(r for r in event.relations if r.object_id != "APRV-A"),
        )
        if event.event_id == "EV-A-09"
        else event
        for event in log.events
        if event.event_id not in ("EV-A-07", "EV-A-08")
    )
    kept_objects = tuple(obj for obj in log.objects if obj.object_id != "APRV-A")
    return OCELLog(events=kept_events, objects=kept_objects, o2o=log.o2o)


def test_clean_vs_clean_is_empty(mini_log: OCELLog) -> None:
    reference = discover_reference(mini_log)
    assert detect(mini_log, reference=reference) == []


def test_novel_edges_cite_real_event_pairs(mini_log: OCELLog) -> None:
    reference = discover_reference(mini_log)
    detections = detect(_delete_approval(mini_log), reference=reference)
    assert detections
    known_event_ids = {event.event_id for event in mini_log.events}
    for violation in detections:
        assert violation.constraint_id == "OCDFG.novel_edge"
        assert len(violation.ocel_event_ids) == 2
        assert set(violation.ocel_event_ids) <= known_event_ids
        assert violation.session_id == "SES-A"  # the faulted session
        assert " -> " in violation.detail


def test_output_is_canonical_and_deterministic(mini_log: OCELLog) -> None:
    reference = discover_reference(mini_log)
    first = detect(_delete_approval(mini_log), reference=reference)
    second = detect(_delete_approval(mini_log), reference=reference)
    assert first == second
    assert first == sorted(first, key=violation_sort_key)

"""The flattened-DECLARE baseline and the E1 detectability matrix (Phase 7).

The mandated test proves the mechanism: qualifier/object-identity loss makes
V2- and V7-shaped faults control-flow-invisible after flattening, while a
V1-shaped fault (event deletion) stays visible — so the test CAN fail.
Corpus-pinned numbers (the probe's measured values) run locally, skipped in CI.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from auditors_trace.baselines.declare_flat import (
    _case_sequences,
    _case_to_session,
    detect,
    discover_reference,
    flatten_to_xes,
    flattening_matrix,
)
from auditors_trace.model.log import OCELLog, OCELRelation
from auditors_trace.model.ocel_schema import Qualifier

REPO = Path(__file__).resolve().parent.parent.parent
GENERATED_SPLITS = REPO / "data" / "generated" / "splits"

full_scale = pytest.mark.skipif(
    not (GENERATED_SPLITS / "single" / "labels.json").exists(),
    reason="full-scale splits absent; run `make scenario` and `scenario inject` first",
)


def _repoint_approves(log: OCELLog) -> OCELLog:
    """The V2 shape: session A's approval endorses session B's decision."""
    events = []
    for event in log.events:
        if event.event_id == "EV-A-08":
            relations = tuple(
                OCELRelation(object_id="DEC-B", qualifier=r.qualifier)
                if r.qualifier is Qualifier.APPROVES
                else r
                for r in event.relations
            )
            event = dataclasses.replace(event, relations=relations)
        events.append(event)
    return OCELLog(events=tuple(events), objects=log.objects, o2o=log.o2o)


def _repoint_handoff(log: OCELLog) -> OCELLog:
    """The V7-repoint shape: session B's handoff targets a third agent
    (never the from-agent — one relation per (event, object) pair)."""
    from auditors_trace.model.log import OCELObject
    from auditors_trace.model.ocel_schema import ObjectType

    third_agent = OCELObject(
        object_id="AGT-THIRD",
        object_type=ObjectType.AGENT,
        attributes=tuple(
            sorted(
                {
                    "agent_id": "AGT-THIRD",
                    "name": "review",
                    "role": "review",
                    "version": "1.0.0",
                    "framework": "langgraph",
                }.items()
            )
        ),
    )
    events = []
    for event in log.events:
        if event.event_id == "EV-B-03":
            relations = tuple(
                OCELRelation(object_id="AGT-THIRD", qualifier=r.qualifier)
                if r.qualifier is Qualifier.TO
                else r
                for r in event.relations
            )
            event = dataclasses.replace(event, relations=relations)
        events.append(event)
    return OCELLog(events=tuple(events), objects=(*log.objects, third_agent), o2o=log.o2o)


def _delete_approval(log: OCELLog) -> OCELLog:
    """The V1 shape: the approval events vanish (control-flow-VISIBLE)."""
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


def test_flattening_loses_synchronisation_violations(mini_log: OCELLog) -> None:
    """The key E1 result: V2 (cross-session approval) and V7 (repointed
    handoff) leave flattened Application activity sequences byte-identical
    to the clean log's — invisible to any case-centric control-flow method —
    while a V1-shaped deletion visibly changes the sequence."""
    clean_sequences = _case_sequences(flatten_to_xes(mini_log, "Application"))

    reference = discover_reference(mini_log, "Application")
    clean_detections = detect(mini_log, case_notion="Application", reference=reference)

    for faulted in (_repoint_approves(mini_log), _repoint_handoff(mini_log)):
        faulted_sequences = _case_sequences(flatten_to_xes(faulted, "Application"))
        assert faulted_sequences == clean_sequences  # control-flow-invisible

        # DECLARE conformance cannot DISTINGUISH the faulted log from the
        # clean one: identical sequences yield identical detections, so the
        # injected fault contributes exactly nothing detectable.
        detections = detect(faulted, case_notion="Application", reference=reference)
        assert detections == clean_detections

    deleted = _delete_approval(mini_log)
    deleted_sequences = _case_sequences(flatten_to_xes(deleted, "Application"))
    assert deleted_sequences != clean_sequences  # the contrast: V1 IS visible


def test_detect_flags_a_visible_fault_with_synthesised_anchors(mini_log: OCELLog) -> None:
    reference = discover_reference(mini_log, "Application")
    detections = detect(_delete_approval(mini_log), case_notion="Application", reference=reference)
    assert detections
    for violation in detections:
        assert violation.constraint_id.startswith("DECLARE.")
        assert violation.ocel_event_ids  # the never-empty contract
        assert violation.session_id in ("SES-A", "SES-B")
    assert detect(
        _delete_approval(mini_log), case_notion="Application", reference=reference
    ) == list(detections)  # deterministic


def test_case_to_session_mapping(mini_log: OCELLog) -> None:
    applications = _case_to_session(mini_log, "Application")
    assert applications == {"APP-A": "SES-A", "APP-B": "SES-B"}
    decisions = _case_to_session(mini_log, "CreditDecision")
    assert decisions == {"DEC-A": "SES-A", "DEC-B": "SES-B"}
    assert _case_to_session(mini_log, "Agent") == {}


def test_flattening_matrix_on_constructed_fixture(mini_log: OCELLog) -> None:
    from auditors_trace.scenario.injector import GroundTruthViolation

    faulted = _repoint_approves(mini_log)
    labels = (
        GroundTruthViolation(
            violation_id="0" * 16,
            fault_class="V2",
            constraint_id="T1.synchronised_approval",
            session_id="SES-A",
            ocel_event_ids=("EV-A-08", "EV-A-09"),
            ocel_object_ids=("DEC-A", "DEC-B"),
            expected_evidence_span_ids=(),
            removed_span_ids=(),
            severity="high",
            articles=("Reg (EU) 2024/1689 Art. 14",),
            description="constructed",
        ),
    )
    matrix = flattening_matrix(mini_log, faulted, labels, case_notions=("Application", "Agent"))
    cell = matrix.cells[("Application", "V2")]
    assert cell.total == 1
    assert cell.control_flow_visible == 0  # the E1 point, cell-level
    application_stats = matrix.notion_stats["Application"]
    assert application_stats.events_dropped == 0
    assert application_stats.events_duplicated == 0
    agent_stats = matrix.notion_stats["Agent"]
    assert agent_stats.events_dropped > 0  # session_start/end carry no Agent


@full_scale
def test_corpus_flattening_numbers_match_the_probe() -> None:
    """The week-3 gate evidence at full scale: the measured blindness table."""
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_run
    from auditors_trace.scenario.injector import read_labels

    clean = map_span_trees(
        build_span_trees(read_run(GENERATED_SPLITS / "clean" / "manifest.json"))
    ).log
    single = map_span_trees(
        build_span_trees(read_run(GENERATED_SPLITS / "single" / "manifest.json"))
    ).log
    labels = read_labels(GENERATED_SPLITS / "single" / "labels.json")

    matrix = flattening_matrix(clean, single, labels.violations, case_notions=("Application",))
    stats = matrix.notion_stats["Application"]
    assert stats.events_dropped == 0
    assert stats.events_duplicated == 0

    visible = {
        fault_class: matrix.cells[("Application", fault_class)].control_flow_visible
        for fault_class in (f"V{i}" for i in range(1, 9))
    }
    assert visible == {
        "V1": 30,
        "V2": 0,
        "V3": 0,
        "V4": 30,
        "V5": 0,
        "V6": 0,
        "V7": 14,
        "V8": 0,
    }
    total_visible = sum(visible.values())
    # 30*5 + 16 = 166 invisible (69.2%) — the probe dossier's "176/73.3%"
    # headline was an arithmetic slip over its own per-class table.
    assert 240 - total_visible == 166

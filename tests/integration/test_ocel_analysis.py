"""Phase 1 "works in real life" evidence, part 2: standard tooling mines us.

Plain pm4py — none of our code — must be able to discover process structure
from our files headlessly (no graphviz binary, no display). If these pass,
any object-centric process-mining stack that speaks OCEL 2.0 can analyse
The Auditor's Trace output.

Never call ``pm4py.ocel_merge_duplicates`` here or anywhere: it mutates its
input OCEL in place and replaces every event id with a fresh UUID.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pm4py
import pytest

MINI_LOG = Path(__file__).resolve().parent.parent / "fixtures" / "mini_log.json"

#: DEC-A's full lifecycle in the fixture, time-ordered.
DEC_A_TRACE = [
    "emit_reasoning",
    "request_approval",
    "grant_approval",
    "make_decision",
    "notify_applicant",
    "session_end",
]
#: DEC-B (denied by the human approver, referred), time-ordered.
DEC_B_TRACE = ["request_approval", "deny_approval", "make_decision", "session_end"]


@pytest.fixture(scope="module")
def ocel() -> Any:
    return pm4py.read_ocel2_json(str(MINI_LOG))


def test_ocdfg_discovers_on_the_fixture(ocel: Any) -> None:
    ocdfg = pm4py.discover_ocdfg(ocel)
    assert set(ocdfg["activities"]) == {
        "session_start",
        "invoke_agent",
        "handoff",
        "call_llm",
        "call_tool",
        "retrieve_data",
        "emit_reasoning",
        "request_approval",
        "grant_approval",
        "deny_approval",
        "make_decision",
        "notify_applicant",
        "session_end",
    }
    assert len(ocdfg["object_types"]) == 12
    assert ocdfg["edges"]  # non-empty control flow


def test_oc_petri_net_discovers_on_the_fixture(ocel: Any) -> None:
    ocpn = pm4py.discover_oc_petri_net(ocel)
    assert ocpn is not None
    assert set(ocpn["object_types"]) == set(pm4py.ocel_get_object_types(ocel))


def test_flattening_credit_decision_yields_both_decision_traces(ocel: Any) -> None:
    flat = pm4py.ocel_flattening(ocel, "CreditDecision")
    assert "ocel:eid" in flat.columns  # event ids survive flattening
    traces = {
        case: list(group.sort_values("time:timestamp")["concept:name"])
        for case, group in flat.groupby("case:concept:name")
    }
    assert traces["DEC-A"] == DEC_A_TRACE
    assert traces["DEC-B"] == DEC_B_TRACE


def test_flattening_runs_for_every_object_type(ocel: Any) -> None:
    for object_type in pm4py.ocel_get_object_types(ocel):
        flat = pm4py.ocel_flattening(ocel, object_type)
        assert len(flat) > 0, object_type


def test_summaries_match_hand_counted_fixture_numbers(ocel: Any) -> None:
    activities_per_type = pm4py.ocel_object_type_activities(ocel)
    assert set(activities_per_type["Tool"]) == {"call_tool"}
    assert set(activities_per_type["HumanApprover"]) == {
        "request_approval",
        "grant_approval",
        "deny_approval",
    }
    temporal = pm4py.ocel_temporal_summary(ocel)
    assert len(temporal) == 18  # 18 events at 18 distinct simulated seconds
    objects_summary = pm4py.ocel_objects_summary(ocel)
    assert len(objects_summary) == 19  # every fixture object has a lifecycle row

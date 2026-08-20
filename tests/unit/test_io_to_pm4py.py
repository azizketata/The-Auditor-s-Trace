"""The in-memory OCELLog -> pm4py bridge (Phase 7).

`to_pm4py` is extracted verbatim from `write_ocel`'s body — the ONE deliberate
touch to the frozen io.py, surfaced in the approved Phase 7 plan. These tests
are its regression guard: the in-memory object must be structurally identical
to what `write_ocel` has always produced, and `write_ocel`'s own behavior must
be unchanged (the golden/roundtrip suites are the second guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auditors_trace.model.io import read_ocel, to_pm4py, write_ocel
from auditors_trace.model.log import OCELLog, OCELModelError, log_hash


def test_to_pm4py_matches_the_written_file(mini_log: OCELLog, tmp_path: Path) -> None:
    """Same events, activities, relations (with qualifiers), objects, and o2o
    as the file `write_ocel` writes."""
    import pm4py

    in_memory = to_pm4py(mini_log)
    path = tmp_path / "mini.jsonocel"
    write_ocel(mini_log, path, "json")
    reread = pm4py.read_ocel2_json(str(path))

    def event_ids(ocel: object) -> set[str]:
        return set(ocel.events["ocel:eid"])  # type: ignore[attr-defined]

    def relation_triples(ocel: object) -> set[tuple[str, str, str]]:
        frame = ocel.relations  # type: ignore[attr-defined]
        return {
            (row[0], row[1], row[2])
            for row in frame[["ocel:eid", "ocel:oid", "ocel:qualifier"]].itertuples(index=False)
        }

    def object_pairs(ocel: object) -> set[tuple[str, str]]:
        frame = ocel.objects  # type: ignore[attr-defined]
        pairs = frame[["ocel:oid", "ocel:type"]].itertuples(index=False)
        return {(row[0], row[1]) for row in pairs}

    assert event_ids(in_memory) == event_ids(reread)
    assert relation_triples(in_memory) == relation_triples(reread)
    assert object_pairs(in_memory) == object_pairs(reread)
    assert len(in_memory.o2o) == len(mini_log.o2o)


def test_to_pm4py_attribute_dtype_semantics(mini_log: OCELLog) -> None:
    """The extracted contract: attribute values are the registry-encoded
    strings write_ocel has always produced; timestamps are pd.Timestamp."""
    ocel = to_pm4py(mini_log)
    events = ocel.events
    objects = ocel.objects

    import pandas as pd

    assert all(isinstance(ts, pd.Timestamp) for ts in events["ocel:timestamp"])

    decision_row = objects[objects["ocel:oid"] == "DEC-B"].iloc[0]
    assert decision_row["reason_codes"] == '["RC03","RC98"]'  # tuple -> canonical JSON string
    assert decision_row["score"] == "441"  # int -> str

    approval_row = objects[objects["ocel:oid"] == "APRV-A"].iloc[0]
    assert approval_row["granted"] == "true"  # bool -> "true"/"false"


def test_to_pm4py_rejects_an_empty_log() -> None:
    with pytest.raises(OCELModelError, match="empty"):
        to_pm4py(OCELLog(events=(), objects=(), o2o=()))


def test_write_ocel_behavior_unchanged(mini_log: OCELLog, tmp_path: Path) -> None:
    """The refactor guard: write -> read round trip still reproduces the model
    exactly (the pinned golden log_hash is the cross-platform anchor)."""
    path = tmp_path / "mini.jsonocel"
    write_ocel(mini_log, path, "json")
    assert log_hash(read_ocel(path, "json")) == log_hash(mini_log)


def test_flattening_runs_on_the_in_memory_ocel(mini_log: OCELLog) -> None:
    """The Phase 7 consumer path: pm4py flattens the in-memory object, event
    ids survive, and no disk round trip is involved."""
    import pm4py

    flat = pm4py.ocel_flattening(to_pm4py(mini_log), "Session")
    assert "ocel:eid" in flat.columns
    assert set(flat["case:concept:name"]) == {"SES-A", "SES-B"}
    assert len(flat) == len(mini_log.events)

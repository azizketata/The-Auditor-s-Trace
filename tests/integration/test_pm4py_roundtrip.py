"""Pin the pm4py OCEL 2.0 platform behaviors the model layer is designed around.

Phase 1 gate spike, 19 Aug 2026 (BUILD-PLAN Phase 1 DoD; docs/
SPIKE-pm4py-roundtrip.md). These tests assert *pm4py's* behavior, not ours:
if a pm4py upgrade changes any of them, the design constraints in
``model/log.py`` need re-deriving, and we want the suite — not a reviewer —
to tell us.

Pinned facts (pm4py 2.7.23.4):

P1. Every OCEL 2.0 exporter (JSON, XML, SQLite) silently deletes objects with
    zero E2O relations, and every O2O edge touching such an object.
P2. With every object reachable through at least one qualified E2O relation
    (our ``declares`` materialization), XML and SQLite round-trip events,
    objects, attributes, E2O qualifiers, O2O qualifiers, and sub-second
    timestamps losslessly.
P3. The JSON *importer* deduplicates E2O relations by (event, object) pair,
    keeping the last — two qualifiers between the same pair survive on disk
    but not through a read. Hence model rule: at most one E2O relation per
    (event, object) pair.
P4. The JSON *exporter* truncates timestamps to whole seconds. Hence model
    rule: OCEL timestamps are whole-second UTC (the simulated clock already
    is, ``scenario/clock.py``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pm4py
import pytest
from pm4py.objects.ocel.obj import OCEL

E, A, T = "ocel:eid", "ocel:activity", "ocel:timestamp"
OID, OT, Q = "ocel:oid", "ocel:type", "ocel:qualifier"

TS0 = pd.Timestamp("2026-03-02T09:00:00.500", tz="UTC")
TS1 = pd.Timestamp("2026-03-02T09:00:05", tz="UTC")
TS2 = pd.Timestamp("2026-03-02T09:00:11", tz="UTC")

FORMATS = [
    pytest.param(pm4py.write_ocel2_json, pm4py.read_ocel2_json, "log.jsonocel", id="json"),
    pytest.param(pm4py.write_ocel2_xml, pm4py.read_ocel2_xml, "log.xmlocel", id="xml"),
    pytest.param(pm4py.write_ocel2_sqlite, pm4py.read_ocel2_sqlite, "log.sqlite", id="sqlite"),
]
LOSSLESS_FORMATS = [p for p in FORMATS if p.id in ("xml", "sqlite")]

Writer = Callable[..., None]
Reader = Callable[..., OCEL]


def _log(*, orphan_policy: bool) -> OCEL:
    """A miniature log; ``orphan_policy`` leaves pol-old without any E2O."""
    events = pd.DataFrame(
        [
            {E: "e0", A: "session_start", T: TS0},
            {E: "e1", A: "grant_approval", T: TS1, "outcome": "grant"},
            {E: "e2", A: "make_decision", T: TS2, "outcome": "grant"},
        ]
    )
    objects = pd.DataFrame(
        [
            {OID: "appr-1", OT: "Approval", "approver_role": "senior_officer"},
            {OID: "dec-1", OT: "CreditDecision", "outcome": "grant"},
            {OID: "pol-old", OT: "PolicyVersion", "version": "2025.11"},
            {OID: "pol-new", OT: "PolicyVersion", "version": "2026.02"},
        ]
    )
    rows = [
        {E: "e1", A: "grant_approval", T: TS1, OID: "appr-1", OT: "Approval", Q: "produces"},
        {E: "e1", A: "grant_approval", T: TS1, OID: "dec-1", OT: "CreditDecision", Q: "approves"},
        {E: "e2", A: "make_decision", T: TS2, OID: "dec-1", OT: "CreditDecision", Q: "produces"},
        {E: "e2", A: "make_decision", T: TS2, OID: "pol-new", OT: "PolicyVersion", Q: "reads"},
    ]
    if not orphan_policy:
        declare = {E: "e0", A: "session_start", T: TS0, OID: "pol-old", OT: "PolicyVersion"}
        rows.insert(0, {**declare, Q: "declares"})
    relations = pd.DataFrame(rows)
    o2o = pd.DataFrame([{OID: "pol-new", OID + "_2": "pol-old", Q: "derived_from"}])
    return OCEL(events=events, objects=objects, relations=relations, o2o=o2o)


def _e2o(log: OCEL) -> set[tuple[str, str, str, str]]:
    return {(r[E], r[OID], r[OT], r[Q]) for _, r in log.relations.iterrows()}


def _roundtrip(log: OCEL, writer: Writer, reader: Reader, path: Path) -> OCEL:
    writer(log, str(path))
    return reader(str(path))


class TestP1OrphanObjectsAreDeleted:
    @pytest.mark.parametrize(("writer", "reader", "fname"), FORMATS)
    def test_exporter_drops_relationless_object_and_its_o2o(
        self, writer: Writer, reader: Reader, fname: str, tmp_path: Path
    ) -> None:
        back = _roundtrip(_log(orphan_policy=True), writer, reader, tmp_path / fname)
        assert "pol-old" not in set(back.objects[OID])
        assert len(back.o2o) == 0

    def test_orphan_never_reaches_the_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonocel"
        pm4py.write_ocel2_json(_log(orphan_policy=True), str(path))
        assert "pol-old" not in path.read_text(encoding="utf-8")


class TestP2DeclaresMaterializationRoundTrips:
    @pytest.mark.parametrize(("writer", "reader", "fname"), FORMATS)
    def test_objects_qualifiers_and_o2o_survive(
        self, writer: Writer, reader: Reader, fname: str, tmp_path: Path
    ) -> None:
        log = _log(orphan_policy=False)
        back = _roundtrip(log, writer, reader, tmp_path / fname)
        assert set(back.objects[OID]) == set(log.objects[OID])
        assert _e2o(back) == _e2o(log)
        assert {(r[OID], r[OID + "_2"], r[Q]) for _, r in back.o2o.iterrows()} == {
            ("pol-new", "pol-old", "derived_from")
        }

    @pytest.mark.parametrize(("writer", "reader", "fname"), FORMATS)
    def test_event_and_object_attributes_survive(
        self, writer: Writer, reader: Reader, fname: str, tmp_path: Path
    ) -> None:
        back = _roundtrip(_log(orphan_policy=False), writer, reader, tmp_path / fname)
        events = back.events.set_index(E)
        objects = back.objects.set_index(OID)
        assert events.loc["e1", "outcome"] == "grant"
        assert objects.loc["appr-1", "approver_role"] == "senior_officer"
        assert objects.loc["pol-old", "version"] == "2025.11"

    @pytest.mark.parametrize(("writer", "reader", "fname"), LOSSLESS_FORMATS)
    def test_subsecond_timestamps_survive_xml_and_sqlite(
        self, writer: Writer, reader: Reader, fname: str, tmp_path: Path
    ) -> None:
        back = _roundtrip(_log(orphan_policy=False), writer, reader, tmp_path / fname)
        stamps = {r[E]: pd.Timestamp(r[T]) for _, r in back.events.iterrows()}
        assert stamps["e0"].microsecond == 500_000


class TestP3JsonImporterDedupsByEventObjectPair:
    def test_second_qualifier_on_same_pair_is_lost_on_read(self, tmp_path: Path) -> None:
        log = _log(orphan_policy=False)
        pair = {E: "e1", A: "grant_approval", T: TS1, OID: "dec-1", OT: "CreditDecision"}
        extra = {**pair, Q: "uses"}
        log.relations = pd.concat([log.relations, pd.DataFrame([extra])], ignore_index=True)
        path = tmp_path / "log.jsonocel"
        pm4py.write_ocel2_json(log, str(path))

        doc = json.loads(path.read_text(encoding="utf-8"))
        e1 = next(e for e in doc["events"] if e["id"] == "e1")
        on_disk = {(r["objectId"], r["qualifier"]) for r in e1["relationships"]}
        assert {("dec-1", "approves"), ("dec-1", "uses")} <= on_disk  # file is fine

        back = pm4py.read_ocel2_json(str(path))
        pairs = [(r[E], r[OID]) for _, r in back.relations.iterrows()]
        assert pairs.count(("e1", "dec-1")) == 1  # the importer deduplicated


class TestP4JsonExporterTruncatesToWholeSeconds:
    def test_subsecond_timestamp_truncated(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonocel"
        pm4py.write_ocel2_json(_log(orphan_policy=False), str(path))
        back = pm4py.read_ocel2_json(str(path))
        stamps = {r[E]: pd.Timestamp(r[T]) for _, r in back.events.iterrows()}
        assert stamps["e0"].microsecond == 0
        assert stamps["e1"] == TS1.tz_localize(None) or stamps["e1"] == TS1

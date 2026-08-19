"""Phase 1 acceptance tests: OCEL 2.0 round trips and the canonical log hash.

The six BUILD-PLAN-mandated names live here as module-level functions.
"Preserves" means model -> write -> read -> model equality (spike constraint
G4: the hash is computed over the model, never file bytes).

The miniature log (``build_mini_log`` in conftest) is the model-side twin of
the hand-written ``tests/fixtures/mini_log.json``. Its design contract: all
13 event types, all 12 object types, all three O2O qualifiers, one pure
declaration reachable only via ``declares`` (the superseded PolicyVersion),
every attribute value kind (str, empty str, bool both ways, int, float,
tuple, empty tuple), two sessions interleaved in time, and cross-session
shared objects (the current PolicyVersion and the intake Agent). The JSON
fixture carries no manifest — it is hand-authored; this docstring is its
documentation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

Builder = Callable[[], Any]

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "mini_log.json"

#: Golden hash of the miniature log. Pinned so every run, every serialisation
#: format, and both CI platforms (Windows + Ubuntu) must agree on one value.
#: Regenerate ONLY on a deliberate model change: log_hash(build_mini_log()).
MINI_LOG_HASH = "8ec53626c97041901e2245dd363944ceac6cb1c5162989a196896d66b2bc6041"

FORMATS = [
    pytest.param("json", "log.jsonocel", id="json"),
    pytest.param("xml", "log.xmlocel", id="xml"),
    pytest.param("sqlite", "log.sqlite", id="sqlite"),
]


def _roundtrip(log: Any, fmt: str, fname: str, tmp_path: Path) -> Any:
    from auditors_trace.model.io import read_ocel, write_ocel

    path = tmp_path / fname
    write_ocel(log, path, fmt)  # type: ignore[arg-type]
    return read_ocel(path, fmt)  # type: ignore[arg-type]


def _object(log: Any, object_id: str) -> Any:
    return next(o for o in log.objects if o.object_id == object_id)


def _event(log: Any, event_id: str) -> Any:
    return next(e for e in log.events if e.event_id == event_id)


def _attr(entity: Any, name: str) -> Any:
    return dict(entity.attributes)[name]


# --- the six mandated acceptance tests -------------------------------------


def test_roundtrip_json_preserves_log(mini_log: Any, tmp_path: Path) -> None:
    assert _roundtrip(mini_log, "json", "log.jsonocel", tmp_path) == mini_log


def test_roundtrip_xml_preserves_log(mini_log: Any, tmp_path: Path) -> None:
    assert _roundtrip(mini_log, "xml", "log.xmlocel", tmp_path) == mini_log


def test_roundtrip_sqlite_preserves_log(mini_log: Any, tmp_path: Path) -> None:
    assert _roundtrip(mini_log, "sqlite", "log.sqlite", tmp_path) == mini_log


def test_log_hash_is_stable_across_runs(mini_log_builder: Builder) -> None:
    from auditors_trace.model.log import log_hash

    first = log_hash(mini_log_builder())
    second = log_hash(mini_log_builder())
    assert first == second
    assert first == MINI_LOG_HASH  # cross-platform via the CI matrix


def test_log_hash_changes_when_any_attribute_changes(mini_log: Any) -> None:
    from dataclasses import replace

    from auditors_trace.model.log import log_hash

    base = mini_log
    baseline = log_hash(base)

    # One mutation per content class: object attr, event attr, timestamp,
    # E2O qualifier, O2O triple, dropped object attr.
    def mutate_object_attr(log: Any) -> Any:
        objs = tuple(
            replace(
                o, attributes=(("band", "B"), *tuple(p for p in o.attributes if p[0] != "band"))
            )
            if o.object_id == "DEC-A"
            else o
            for o in log.objects
        )
        return replace(log, objects=objs)

    def mutate_event_attr(log: Any) -> Any:
        evs = tuple(
            replace(
                e,
                attributes=tuple(
                    ("score", 613) if n == "score" else (n, v) for n, v in e.attributes
                ),
            )
            if e.event_id == "EV-A-09"
            else e
            for e in log.events
        )
        return replace(log, events=evs)

    def mutate_timestamp(log: Any) -> Any:
        evs = tuple(
            replace(e, timestamp="2026-03-02T09:00:16Z") if e.event_id == "EV-A-09" else e
            for e in log.events
        )
        return replace(log, events=evs)

    def mutate_qualifier(log: Any) -> Any:
        # (session_start, Session) legally admits both `declares` and
        # `within` — the one pair where a qualifier flip stays valid.
        from auditors_trace.model.log import OCELRelation
        from auditors_trace.model.ocel_schema import Qualifier

        evs = tuple(
            replace(
                e,
                relations=tuple(
                    OCELRelation(object_id=r.object_id, qualifier=Qualifier.WITHIN)
                    if r.object_id == "SES-A" and r.qualifier is Qualifier.DECLARES
                    else r
                    for r in e.relations
                ),
            )
            if e.event_id == "EV-A-01"
            else e
            for e in log.events
        )
        return replace(log, events=evs)

    def mutate_o2o(log: Any) -> Any:
        return replace(log, o2o=log.o2o[:-1])

    def drop_object_attr(log: Any) -> Any:
        objs = tuple(
            replace(o, attributes=tuple(p for p in o.attributes if p[0] != "controller"))
            if o.object_id == "RES-BUREAU"
            else o
            for o in log.objects
        )
        return replace(log, objects=objs)

    mutations = [
        mutate_object_attr,
        mutate_event_attr,
        mutate_timestamp,
        mutate_qualifier,
        mutate_o2o,
        drop_object_attr,
    ]
    hashes = [log_hash(m(base)) for m in mutations]
    assert baseline not in hashes
    assert len(set(hashes)) == len(hashes)  # each mutation is distinguishable


@pytest.mark.parametrize(("fmt", "fname"), FORMATS)
def test_qualified_relations_survive_roundtrip(
    mini_log: Any, fmt: str, fname: str, tmp_path: Path
) -> None:
    log = mini_log
    back = _roundtrip(log, fmt, fname, tmp_path)

    def e2o(entry: Any) -> set[tuple[str, str, str]]:
        return {
            (e.event_id, r.object_id, r.qualifier.value) for e in entry.events for r in e.relations
        }

    def o2o(entry: Any) -> set[tuple[str, str, str]]:
        return {(r.source_id, r.target_id, r.qualifier.value) for r in entry.o2o}

    assert e2o(back) == e2o(log)
    assert o2o(back) == o2o(log)
    # The pure declaration survived, reachable only via `declares`.
    assert ("EV-A-01", "POL-CS-2025-11", "declares") in e2o(back)


# --- supporting round-trip evidence ----------------------------------------


@pytest.mark.parametrize(("fmt", "fname"), FORMATS)
def test_attribute_value_kinds_roundtrip(
    mini_log: Any, fmt: str, fname: str, tmp_path: Path
) -> None:
    """Every representable value kind survives every serialisation, typed."""
    back = _roundtrip(mini_log, fmt, fname, tmp_path)

    assert _attr(_object(back, "RES-BUREAU"), "lawful_basis") == ""  # empty str
    assert _attr(_object(back, "APRV-A"), "granted") is True  # bool
    assert _attr(_object(back, "APRV-B"), "granted") is False
    assert _attr(_object(back, "APP-A"), "amount") == 4870  # int, not str/float
    assert isinstance(_attr(_object(back, "APP-A"), "amount"), int)
    temperature = _attr(_event(back, "EV-A-03"), "temperature")
    assert temperature == 0.0
    assert isinstance(temperature, float)
    assert _attr(_object(back, "DEC-B"), "reason_codes") == ("RC03", "RC98")  # tuple
    assert _attr(_object(back, "DEC-A"), "reason_codes") == ()  # empty tuple
    adverse = _attr(_event(back, "EV-B-06"), "adverse")
    assert adverse is True


@pytest.mark.parametrize(("fmt", "fname"), FORMATS)
def test_write_is_byte_deterministic(mini_log: Any, fmt: str, fname: str, tmp_path: Path) -> None:
    from auditors_trace.model.io import write_ocel

    log = mini_log
    a, b = tmp_path / f"a_{fname}", tmp_path / f"b_{fname}"
    write_ocel(log, a, fmt)  # type: ignore[arg-type]
    write_ocel(log, b, fmt)  # type: ignore[arg-type]
    assert hashlib.sha256(a.read_bytes()).hexdigest() == hashlib.sha256(b.read_bytes()).hexdigest()


def test_hash_invariant_across_formats(mini_log: Any, tmp_path: Path) -> None:
    """One model, three files, one hash — the G4 claim end to end."""
    from auditors_trace.model.io import read_ocel, write_ocel
    from auditors_trace.model.log import log_hash

    log = mini_log
    hashes = {log_hash(log)}
    for fmt, fname in (("json", "h.jsonocel"), ("xml", "h.xmlocel"), ("sqlite", "h.sqlite")):
        path = tmp_path / fname
        write_ocel(log, path, fmt)  # type: ignore[arg-type]
        hashes.add(log_hash(read_ocel(path, fmt)))  # type: ignore[arg-type]
    assert hashes == {MINI_LOG_HASH}


def test_detect_serialisation() -> None:
    from auditors_trace.model.io import detect_serialisation
    from auditors_trace.model.log import OCELModelError

    assert detect_serialisation(Path("x.json")) == "json"
    assert detect_serialisation(Path("x.jsonocel")) == "json"
    assert detect_serialisation(Path("x.xml")) == "xml"
    assert detect_serialisation(Path("x.xmlocel")) == "xml"
    assert detect_serialisation(Path("x.sqlite")) == "sqlite"
    assert detect_serialisation(Path("x.sqlite3")) == "sqlite"
    assert detect_serialisation(Path("x.db")) == "sqlite"
    with pytest.raises(OCELModelError):
        detect_serialisation(Path("x.csv"))


# --- the hand-written fixture ----------------------------------------------


def test_mini_log_fixture_matches_model(mini_log: Any) -> None:
    """The JSON file and the Python builder are the same log."""
    from auditors_trace.model.io import read_ocel

    assert read_ocel(FIXTURE, "json") == mini_log


def test_mini_log_fixture_readable_by_pm4py() -> None:
    """Plain pm4py — no code of ours — can consume the fixture."""
    import pm4py

    ocel = pm4py.read_ocel2_json(str(FIXTURE))
    assert len(ocel.events) == 18
    assert len(ocel.objects) == 19
    assert ocel.is_ocel20()


# --- adversarial reads -----------------------------------------------------


def test_read_rejects_unknown_qualifier(mini_log: Any, tmp_path: Path) -> None:
    from auditors_trace.model.io import read_ocel, write_ocel
    from auditors_trace.model.log import OCELModelError

    path = tmp_path / "bad.jsonocel"
    write_ocel(mini_log, path, "json")
    text = path.read_text(encoding="utf-8").replace('"declares"', '"frobnicates"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(OCELModelError):
        read_ocel(path, "json")


def test_read_rejects_unknown_attribute_name(mini_log: Any, tmp_path: Path) -> None:
    from auditors_trace.model.io import read_ocel, write_ocel
    from auditors_trace.model.log import OCELModelError

    path = tmp_path / "bad.jsonocel"
    write_ocel(mini_log, path, "json")
    text = path.read_text(encoding="utf-8").replace('"lawful_basis"', '"lawless_basis"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(OCELModelError):
        read_ocel(path, "json")


def test_foreign_null_string_reads_as_absent_attribute(mini_log: Any, tmp_path: Path) -> None:
    """pm4py's JSON importer maps the literal string "null" to None.

    Our own writer can never produce it (model rule R5); a foreign file that
    does simply loses the attribute — pinned here so the behavior is a
    documented fact, not a surprise.
    """
    from auditors_trace.model.io import read_ocel, write_ocel

    path = tmp_path / "foreign.jsonocel"
    write_ocel(mini_log, path, "json")
    original = path.read_text(encoding="utf-8")
    text = original.replace('"bureau_gmbh"', '"null"')
    assert text != original  # the replace matched
    path.write_text(text, encoding="utf-8")
    back = read_ocel(path, "json")
    assert "controller" not in dict(_object(back, "RES-BUREAU").attributes)

"""Phase 4 acceptance: split generation, determinism, and label recoverability.

One scripted 16-session fleet run (session-scoped) feeds every test; split
sizes are scaled (clean=4, single=8, mixed=4) so quotas are 1 per class.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("opentelemetry")

REPO = Path(__file__).resolve().parent.parent.parent
CATALOGUE = REPO / "data" / "catalogue" / "violations.yaml"
FIXTURE_SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"

SIZES = {"clean": 4, "single": 8, "mixed": 4}


@pytest.fixture(scope="session")
def base_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from auditors_trace.scenario.agents import run_fleet

    out = tmp_path_factory.mktemp("base") / "spans"
    run_fleet(16, 7, out, provider="scripted", seed_data=FIXTURE_SAMPLE, quiet=True)
    return out


@pytest.fixture(scope="session")
def splits(base_run: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    from auditors_trace.scenario.injector import inject_run, load_catalogue

    out = tmp_path_factory.mktemp("splits")
    inject_run(base_run, out, 7, load_catalogue(CATALOGUE), SIZES)
    return out


def _map_split(split_dir: Path) -> Any:
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_run

    return map_span_trees(build_span_trees(read_run(split_dir / "manifest.json")))


def test_injector_is_deterministic_given_seed(
    base_run: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from auditors_trace.scenario.injector import inject_run, load_catalogue

    catalogue = load_catalogue(CATALOGUE)
    a = tmp_path_factory.mktemp("det_a")
    b = tmp_path_factory.mktemp("det_b")
    inject_run(base_run, a, 7, catalogue, SIZES)
    inject_run(base_run, b, 7, catalogue, SIZES)
    files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel

    c = tmp_path_factory.mktemp("det_c")
    inject_run(base_run, c, 8, catalogue, SIZES)
    labels_a = (a / "single" / "labels.json").read_bytes()
    labels_c = (c / "single" / "labels.json").read_bytes()
    assert labels_a != labels_c  # a different seed differs


def test_clean_split_contains_zero_injected_violations(base_run: Path, splits: Path) -> None:
    from auditors_trace.scenario.injector import read_labels

    labels = read_labels(splits / "clean" / "labels.json")
    assert labels.violations == ()
    assert labels.distractors == ()
    # Every clean file is byte-identical to its base-run original.
    base_manifest = json.loads((base_run / "manifest.json").read_text(encoding="utf-8"))
    base_digests = dict(tuple(pair) for pair in base_manifest["files"])
    clean_manifest = json.loads((splits / "clean" / "manifest.json").read_text(encoding="utf-8"))
    for filename, digest in clean_manifest["files"]:
        assert digest == base_digests[filename], filename


def test_injected_violation_ids_are_recoverable_from_log(splits: Path) -> None:
    from auditors_trace.scenario.injector import read_labels

    for split in ("single", "mixed"):
        run = _map_split(splits / split)
        event_ids = {e.event_id for e in run.log.events}
        object_ids = {o.object_id for o in run.log.objects}
        index_span_ids = {
            span_id
            for ref in run.span_index.events
            for span_id in (ref.span_id, *ref.enrichment_span_ids)
        } | {
            span_id
            for s in run.span_index.sessions
            for span_id in (s.root_span_id, *s.session_span_ids)
        }
        labels = read_labels(splits / split / "labels.json")
        assert labels.violations, split
        for violation in labels.violations:
            assert set(violation.ocel_event_ids) <= event_ids, violation.violation_id
            assert set(violation.ocel_object_ids) <= object_ids, violation.violation_id
            assert set(violation.expected_evidence_span_ids) <= index_span_ids
            assert not (set(violation.removed_span_ids) & index_span_ids)

        # The content-addressed id re-derives from labels content alone.
        import hashlib

        for violation in labels.violations:
            payload = {
                "base_run_id": labels.base_run_id,
                "catalogue_sha256": labels.violations_catalogue_sha256,
                "constraint_id": violation.constraint_id,
                "fault_class": violation.fault_class,
                "injector_seed": labels.injector_seed,
                "ocel_event_ids": sorted(violation.ocel_event_ids),
                "removed_span_ids": sorted(violation.removed_span_ids),
                "session_id": violation.session_id,
            }
            digest = hashlib.sha256(
                json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest()[:16]
            assert digest == violation.violation_id


def test_composed_injections_have_consistent_ground_truth(splits: Path) -> None:
    from auditors_trace.scenario.injector import load_catalogue, read_labels

    catalogue = load_catalogue(CATALOGUE)
    compatible = {e.entry_id: set(e.co_injectable_with) for e in catalogue.entries}
    labels = read_labels(splits / "mixed" / "labels.json")

    by_session: dict[str, list[str]] = {}
    for violation in labels.violations:
        by_session.setdefault(violation.session_id, []).append(violation.fault_class)
    for session_id, classes in by_session.items():
        assert 1 <= len(classes) <= 3, session_id
        for a in classes:
            for b in classes:
                if a != b:
                    assert b in compatible[a], (session_id, a, b)

    # No duplicate (constraint, event-ids) ground-truth pairs.
    keys = [(v.constraint_id, tuple(sorted(v.ocel_event_ids))) for v in labels.violations]
    assert len(keys) == len(set(keys))

    # The composed split maps cleanly (asserted via the recoverability test's
    # mapping too, but pin it here for the mixed split explicitly).
    run = _map_split(splits / "mixed")
    assert run.log.events

    # V2 donors are never decisions of T1-faulted sessions in the split:
    # V1/V2 labels carry the faulted session's decision id at index 0.
    t1_faulted_decisions = {
        v.ocel_object_ids[0] for v in labels.violations if v.fault_class in ("V1", "V2")
    }
    for v in labels.violations:
        if v.fault_class == "V2":
            donor_decision = v.ocel_object_ids[1]
            assert donor_decision not in t1_faulted_decisions, v.violation_id


def test_each_split_reaches_its_quota(splits: Path) -> None:
    from auditors_trace.scenario.injector import read_labels

    single = read_labels(splits / "single" / "labels.json")
    counts: dict[str, int] = {}
    for violation in single.violations:
        counts[violation.fault_class] = counts.get(violation.fault_class, 0) + 1
    assert counts == {f"V{i}": 1 for i in range(1, 9)}  # quota = 8/8 = 1 each


def test_infeasible_sizes_raise_actionably(base_run: Path, tmp_path: Path) -> None:
    from auditors_trace.scenario.injector import (
        InjectionError,
        inject_run,
        load_catalogue,
    )

    catalogue = load_catalogue(CATALOGUE)
    with pytest.raises(InjectionError, match="partition the run exactly"):
        inject_run(base_run, tmp_path, 7, catalogue, {"clean": 1, "single": 8, "mixed": 1})
    with pytest.raises(InjectionError, match="divisible"):
        inject_run(base_run, tmp_path, 7, catalogue, {"clean": 5, "single": 7, "mixed": 4})


def test_catalogue_hash_matches_tag() -> None:
    from auditors_trace.scenario.injector import catalogue_hash

    proc = subprocess.run(
        ["git", "tag", "-l", "catalogue-v1"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    if "catalogue-v1" not in proc.stdout:
        pytest.skip("catalogue-v1 not yet tagged - freeze is gated on Study A")
    message = subprocess.run(
        ["git", "tag", "-l", "--format=%(contents)", "catalogue-v1"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    ).stdout
    expected = next(
        line.split("=", 1)[1].strip() for line in message.splitlines() if line.startswith("sha256=")
    )
    assert catalogue_hash(CATALOGUE) == expected


class TestInjectCli:
    def test_usage_and_happy_path(self, base_run: Path, tmp_path: Path) -> None:
        from auditors_trace.scenario.__main__ import main

        assert main(["inject", "--seed", "7", "--sizes", "bogus"]) == 2
        assert (
            main(
                [
                    "inject",
                    "--spans",
                    str(tmp_path / "ghost"),
                    "--seed",
                    "7",
                    "--out",
                    str(tmp_path / "out"),
                ]
            )
            == 3
        )
        code = main(
            [
                "inject",
                "--spans",
                str(base_run),
                "--seed",
                "7",
                "--out",
                str(tmp_path / "out"),
                "--sizes",
                "clean=4,single=8,mixed=4",
                "--quiet",
            ]
        )
        assert code == 0
        for split in ("clean", "single", "mixed"):
            assert (tmp_path / "out" / split / "manifest.json").exists()
            assert (tmp_path / "out" / split / "labels.json").exists()

    def test_infeasible_sizes_exit_2(self, base_run: Path, tmp_path: Path) -> None:
        from auditors_trace.scenario.__main__ import main

        code = main(
            [
                "inject",
                "--spans",
                str(base_run),
                "--seed",
                "7",
                "--out",
                str(tmp_path / "out"),
                "--sizes",
                "clean=5,single=7,mixed=4",
                "--quiet",
            ]
        )
        assert code == 2

"""Phase 5 acceptance: engine determinism, recall on injected splits, precision.

Recall 1.0 here is VERIFICATION of the templates against pre-registered ground
truth (PLAN-REVIEW B2) — a test, never a reported result. Reported numbers
come from the held-out fault set authored after the template freeze.

Hermetic path (CI): one scripted 16-session run -> injected splits (4/8/4,
quota 1 per class) -> mapped in-test, mirroring test_injection.py. Full-scale
path: skip-guarded on data/generated/splits being present locally; each split
is re-mapped from its own manifest in-test because the convenience OCEL files
under data/generated/ocel can go stale against regenerated splits (the
manifest's sha256 verification guarantees label<->log consistency; a cached
.jsonocel guarantees nothing).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("opentelemetry")

from auditors_trace.constraints.engine import evaluate
from auditors_trace.constraints.ruleset import RuleSet, load_ruleset
from auditors_trace.eval.metrics import ConfusionMatrix, confusion, precision_recall_f1

if TYPE_CHECKING:
    from auditors_trace.constraints.templates import Violation
    from auditors_trace.model.log import OCELLog
    from auditors_trace.scenario.injector import GroundTruthViolation, LabelsFile

REPO = Path(__file__).resolve().parent.parent.parent
CATALOGUE = REPO / "data" / "catalogue" / "violations.yaml"
RULES = REPO / "rules" / "rules.yaml"
FIXTURE_SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"
GENERATED_SPLITS = REPO / "data" / "generated" / "splits"

SIZES = {"clean": 4, "single": 8, "mixed": 4}

full_scale = pytest.mark.skipif(
    not (GENERATED_SPLITS / "single" / "labels.json").exists(),
    reason="full-scale splits absent; run `make scenario` and `scenario inject` first",
)


@pytest.fixture(scope="session")
def ruleset() -> RuleSet:
    return load_ruleset(RULES)


@pytest.fixture(scope="session")
def engine_base_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from auditors_trace.scenario.agents import run_fleet

    out = tmp_path_factory.mktemp("engine_base") / "spans"
    run_fleet(16, 7, out, provider="scripted", seed_data=FIXTURE_SAMPLE, quiet=True)
    return out


@pytest.fixture(scope="session")
def engine_splits(engine_base_run: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    from auditors_trace.scenario.injector import inject_run, load_catalogue

    out = tmp_path_factory.mktemp("engine_splits")
    inject_run(engine_base_run, out, 7, load_catalogue(CATALOGUE), SIZES)
    return out


def _map_split(split_dir: Path) -> OCELLog:
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_run

    return map_span_trees(build_span_trees(read_run(split_dir / "manifest.json"))).log


def _labels(split_dir: Path) -> LabelsFile:
    from auditors_trace.scenario.injector import read_labels

    return read_labels(split_dir / "labels.json")


def test_engine_is_deterministic(engine_splits: Path, ruleset: RuleSet) -> None:
    """100 runs on the same log: identical violation set AND order."""
    log = _map_split(engine_splits / "single")
    first = evaluate(log, ruleset)
    for _ in range(99):
        assert evaluate(log, ruleset) == first

    # Two independent read paths cannot disagree either.
    assert evaluate(_map_split(engine_splits / "single"), ruleset) == first


def test_engine_finds_all_injected_on_single_split(engine_splits: Path, ruleset: RuleSet) -> None:
    """Recall 1.0 on the single split — the smoke test for the whole approach.

    Exact-match join on (constraint_id, event ids): every one of the eight
    injected violations (one per fault class) is found with its pre-registered
    anchor, and nothing else fires. Per B2 this is verification of the
    templates against ground truth the same author wrote — never a reported
    result; the reportable experiment uses the held-out fault set.
    """
    log = _map_split(engine_splits / "single")
    labels = _labels(engine_splits / "single")
    assert len(labels.violations) == 8
    matrix = confusion(evaluate(log, ruleset), labels.violations)
    assert matrix == ConfusionMatrix(true_positives=8, false_positives=0, false_negatives=0)
    assert precision_recall_f1(matrix) == (1.0, 1.0, 1.0)


def test_engine_zero_violations_on_clean_split(engine_splits: Path, ruleset: RuleSet) -> None:
    """The precision check — simultaneously proves the declares-exclusion,
    the refer-outcome exclusion, and STD's silence on declared-but-not-
    governing superseded policies."""
    log = _map_split(engine_splits / "clean")
    assert _labels(engine_splits / "clean").violations == ()
    assert evaluate(log, ruleset) == []


def test_engine_recall_on_mixed_split(engine_splits: Path, ruleset: RuleSet) -> None:
    log = _map_split(engine_splits / "mixed")
    labels = _labels(engine_splits / "mixed")
    assert labels.violations  # the composer must have injected something
    matrix = confusion(evaluate(log, ruleset), labels.violations)
    assert matrix.false_negatives == 0
    assert matrix.false_positives == 0
    assert matrix.true_positives == len(labels.violations)


def test_distractor_sessions_produce_no_t1_violations(
    engine_splits: Path, ruleset: RuleSet
) -> None:
    """D1 (in-roster boundary approver) and D2 (refer without approval) are
    near-misses by construction: no T1 violation may fire in their sessions
    unless the ground truth says so."""
    log = _map_split(engine_splits / "single")
    labels = _labels(engine_splits / "single")
    distractor_sessions = {d.session_id for d in labels.distractors}
    if not distractor_sessions:
        pytest.skip("this hermetic run allocated no distractors")
    t1_truth_sessions = {
        v.session_id for v in labels.violations if v.constraint_id.startswith("T1.")
    }
    for violation in evaluate(log, ruleset):
        if violation.constraint_id.startswith("T1.") and violation.session_id:
            assert (
                violation.session_id not in distractor_sessions
                or violation.session_id in t1_truth_sessions
            ), violation


# --- Full-scale verification against the committed corpus (local only) ------


@pytest.fixture(scope="session")
def full_single(ruleset: RuleSet) -> tuple[list[Violation], LabelsFile]:
    log = _map_split(GENERATED_SPLITS / "single")
    return evaluate(log, ruleset), _labels(GENERATED_SPLITS / "single")


@full_scale
def test_full_scale_single_recall_and_precision(
    full_single: tuple[list[Violation], LabelsFile],
) -> None:
    """240/240 exact matches: recall AND precision 1.0 on the live corpus.

    B2 applies: verification, never a reported result.
    """
    predicted, labels = full_single
    assert len(labels.violations) == 240
    matrix = confusion(predicted, labels.violations)
    assert matrix == ConfusionMatrix(true_positives=240, false_positives=0, false_negatives=0)


@full_scale
def test_full_scale_per_fault_variant_coverage(
    full_single: tuple[list[Violation], LabelsFile],
) -> None:
    """Every fault class is fully matched, and the surgery-mode variants are
    all represented and found: V7's delete (one-event anchor) vs repoint
    (handoff+action anchor), V4's leaf vs tool-call deletions (one vs several
    removed spans). Variant axes that leave no labels-visible trace (V6 A/B)
    are covered by the exactness of the global 240/240 join."""
    from collections import Counter

    predicted, labels = full_single
    matched_keys = {
        (violation.constraint_id, frozenset(violation.ocel_event_ids)) for violation in predicted
    }

    def is_matched(truth: GroundTruthViolation) -> bool:
        return (truth.constraint_id, frozenset(truth.ocel_event_ids)) in matched_keys

    by_class: dict[str, list[GroundTruthViolation]] = {}
    for truth in labels.violations:
        by_class.setdefault(truth.fault_class, []).append(truth)
    assert Counter({cls: len(items) for cls, items in by_class.items()}) == Counter(
        {f"V{i}": 30 for i in range(1, 9)}
    )
    for fault_class, items in sorted(by_class.items()):
        assert all(is_matched(t) for t in items), fault_class

    v7_modes = {len(t.ocel_event_ids) for t in by_class["V7"]}
    assert v7_modes == {1, 2}  # delete AND repoint both occurred and matched

    v4_modes = {min(len(t.removed_span_ids), 2) for t in by_class["V4"]}
    assert v4_modes == {1, 2}  # leaf AND tool-call deletions both occurred


@full_scale
def test_full_scale_mixed_recall(ruleset: RuleSet) -> None:
    log = _map_split(GENERATED_SPLITS / "mixed")
    labels = _labels(GENERATED_SPLITS / "mixed")
    matrix = confusion(evaluate(log, ruleset), labels.violations)
    assert matrix.false_negatives == 0
    assert matrix.false_positives == 0


@full_scale
def test_full_scale_clean_is_silent(ruleset: RuleSet) -> None:
    log = _map_split(GENERATED_SPLITS / "clean")
    assert evaluate(log, ruleset) == []

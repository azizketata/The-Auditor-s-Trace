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
    unless the ground truth says so.

    Adversarial-review hardening: T1 silence alone is implied by the fp==0
    recall test, so this also asserts the D1 near-miss EXISTS in the mapped
    log (the swapped risk_manager approver) — the substance a no-op surgery
    regression would erase. The injector-side twin lives in
    test_injection.py::test_d1_surgery_lands_in_the_spans."""
    from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier

    log = _map_split(engine_splits / "single")
    labels = _labels(engine_splits / "single")
    distractor_sessions = {d.session_id for d in labels.distractors}
    d1_sessions = {d.session_id for d in labels.distractors if d.kind == "D1_authority_boundary"}
    assert d1_sessions, "the hermetic single split must allocate at least one D1 distractor"

    approver_role = {
        o.object_id: dict(o.attributes).get("role")
        for o in log.objects
        if o.object_type is ObjectType.HUMAN_APPROVER
    }
    verdict_types = (EventType.GRANT_APPROVAL, EventType.DENY_APPROVAL)
    for session_id in sorted(d1_sessions):
        roles = {
            approver_role[relation.object_id]
            for event in log.events
            if event.event_type in verdict_types
            and any(
                r.qualifier is Qualifier.WITHIN and r.object_id == session_id
                for r in event.relations
            )
            for relation in event.relations
            if relation.qualifier is Qualifier.PERFORMED_BY and relation.object_id in approver_role
        }
        assert roles == {"risk_manager"}, (session_id, roles)

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


def test_variant_anchors_survive_the_pipeline(
    engine_base_run: Path, ruleset: RuleSet, tmp_path: Path
) -> None:
    """Adversarial-review regression: the hermetic corpus (seed 7) only ever
    realises V7-repoint and V4-leaf, so the delete-mode / tool-call / V6
    variant anchor joins (injector surgery -> mapper -> template citation)
    were CI-untested. Force every variant of the variant-axis classes through
    the full pipeline and assert the engine's citation equals the
    pre-registered anchor exactly, with nothing else firing."""
    import json

    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_spans
    from auditors_trace.scenario.injector import (
        REGISTRY,
        FaultContext,
        RunView,
        ViolationDraft,
        _recompute_session_end,
        _renumber_seq,
        _session_view,
        load_catalogue,
    )

    catalogue = load_catalogue(CATALOGUE)
    entries = {e.entry_id: e for e in catalogue.entries}
    sessions = []
    for span_file in sorted(engine_base_run.glob("*.jsonl")):
        rows = tuple(
            json.loads(line) for line in span_file.read_text(encoding="utf-8").split("\n") if line
        )
        sessions.append((_session_view(rows), rows))

    def eligible_rows(entry_id: str) -> tuple[object, tuple[dict[str, object], ...]]:
        registration = REGISTRY[entries[entry_id].injection_function]
        for view, rows in sessions:
            if registration.eligible(view):
                return view, rows
        raise AssertionError(f"no eligible session for {entry_id} in the hermetic run")

    def run_variant(entry_id: str, seed: int, out_name: str) -> tuple[ViolationDraft, object]:
        view, rows = eligible_rows(entry_id)
        entry = entries[entry_id]
        ctx = FaultContext(
            seed=seed,
            entry=entry,
            session=view,
            run=RunView(sessions=(view,)),
            variant_key=f"anchor-{seed}-{entry_id}-{view.session_id}",
        )
        result = REGISTRY[entry.injection_function].fn(rows, ctx)
        final_rows = _recompute_session_end(_renumber_seq(result.rows))
        out = tmp_path / f"{out_name}.jsonl"
        out.write_text(
            "\n".join(
                json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                for r in final_rows
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return result.draft, map_span_trees(build_span_trees(read_spans(out)))

    def v6_variant(draft: ViolationDraft, run: object) -> str:
        decision_id = draft.ocel_object_ids[0]
        codes = next(
            dict(o.attributes).get("reason_codes")
            for o in run.log.objects  # type: ignore[attr-defined]
            if o.object_id == decision_id
        )
        return "blank_codes" if codes == () else "strip_explains"

    classifiers = {
        "V7": lambda draft, run: "delete" if len(draft.ocel_event_ids) == 1 else "repoint",
        "V4": lambda draft, run: "leaf" if len(draft.removed_span_ids) == 1 else "tool_call",
        "V6": v6_variant,
    }
    wanted = {
        "V7": {"delete", "repoint"},
        "V4": {"leaf", "tool_call"},
        "V6": {"blank_codes", "strip_explains"},
    }
    for entry_id, variants in sorted(wanted.items()):
        seen: set[str] = set()
        for seed in range(80):
            if seen == variants:
                break
            draft, run = run_variant(entry_id, seed, f"{entry_id.lower()}s{seed}")
            variant = classifiers[entry_id](draft, run)
            if variant in seen:
                continue
            seen.add(variant)
            predicted = evaluate(run.log, ruleset)  # type: ignore[attr-defined]
            assert [(v.constraint_id, frozenset(v.ocel_event_ids)) for v in predicted] == [
                (entries[entry_id].constraint_id, frozenset(draft.ocel_event_ids))
            ], (entry_id, variant, predicted)
        assert seen == variants, (entry_id, seen)

"""Detection, determinism, reproducibility, and scalability metrics.

Phase 8 deliverable, except ``confusion``/``precision_recall_f1`` and the
default :func:`exact_match` predicate, pulled forward into Phase 5: invariant
I4 freezes the ground-truth matching function together with the catalogue and
the ruleset, and the engine's recall-1.0 verification (B2: a test, never a
reported result) needs the pinned predicate now. The judge's looser
overlap-threshold matcher (Phase 7/8) plugs into the ``matcher`` seam; it
never replaces the default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auditors_trace.constraints.templates import Violation
from auditors_trace.scenario.injector import GroundTruthViolation

if TYPE_CHECKING:
    from collections.abc import Callable

    Matcher = Callable[[Violation, GroundTruthViolation], bool]


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    """True positives, false positives, false negatives."""

    true_positives: int
    false_positives: int
    false_negatives: int


def exact_match(predicted: Violation, truth: GroundTruthViolation) -> bool:
    """The pre-registered engine-verification matcher (frozen at I4).

    A prediction matches a ground-truth violation iff the constraint id is
    identical and the event-id SETS are equal — the anchors were pre-registered
    per fault class, so subset or superset citations are misses, not credit.
    Object ids never participate (V4's ground truth legitimately has none).
    """
    return predicted.constraint_id == truth.constraint_id and frozenset(
        predicted.ocel_event_ids
    ) == frozenset(truth.ocel_event_ids)


def confusion(
    predicted: list[Violation],
    truth: tuple[GroundTruthViolation, ...],
    *,
    matcher: Matcher = exact_match,
) -> ConfusionMatrix:
    """Match predictions against ground truth by (constraint id, event ids).

    Deterministic greedy 1:1 matching: ground truths are visited in sorted
    order, predictions in the order given (the engine's canonical order);
    each prediction matches at most one truth, so a duplicate prediction of
    an already-matched truth counts as a false positive.
    """
    remaining = list(predicted)
    true_positives = 0
    false_negatives = 0
    ordered = sorted(
        truth,
        key=lambda t: (t.constraint_id, tuple(sorted(t.ocel_event_ids)), t.violation_id),
    )
    for ground_truth in ordered:
        for index, prediction in enumerate(remaining):
            if matcher(prediction, ground_truth):
                true_positives += 1
                del remaining[index]
                break
        else:
            false_negatives += 1
    return ConfusionMatrix(
        true_positives=true_positives,
        false_positives=len(remaining),
        false_negatives=false_negatives,
    )


def precision_recall_f1(matrix: ConfusionMatrix) -> tuple[float, float, float]:
    """Return precision, recall, and F1.

    Vacuous conventions, documented rather than accidental: with zero
    predictions, precision is 1.0 only when nothing was missed either (the
    clean-split perfect outcome) and 0.0 otherwise; recall is symmetric with
    zero ground truths; F1 is 0.0 whenever precision + recall is 0.
    """
    tp, fp, fn = matrix.true_positives, matrix.false_positives, matrix.false_negatives
    if tp + fp:
        precision = tp / (tp + fp)
    else:
        precision = 1.0 if fn == 0 else 0.0
    if tp + fn:
        recall = tp / (tp + fn)
    else:
        recall = 1.0 if fp == 0 else 0.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return (precision, recall, f1)


def determinism_score(verdict_sets: list[list[Violation]]) -> float:
    """D = runs producing the modal verdict set, over total runs.

    Expected to be 1.0 for the engine and below 1.0 for the judge baseline.
    """
    raise NotImplementedError


def evidence_reproducibility(record_hashes: list[list[str]]) -> float:
    """Fraction of records whose hash is identical across every run."""
    raise NotImplementedError

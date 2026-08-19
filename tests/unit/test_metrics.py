"""The ground-truth matching function and derived scores (pulled forward, I4).

`confusion` with its default `exact_match` predicate is the matching function
the freeze commit pins (invariant I4): a prediction matches a ground-truth
violation iff the constraint id is identical and the event-id SETS are equal.
The judge's looser overlap matcher (Phase 7/8) plugs into the same `matcher`
seam; it never replaces the default.
"""

from __future__ import annotations

from auditors_trace.constraints.templates import Violation
from auditors_trace.eval.metrics import (
    ConfusionMatrix,
    confusion,
    exact_match,
    precision_recall_f1,
)
from auditors_trace.scenario.injector import GroundTruthViolation


def _truth(
    constraint_id: str = "T1.synchronised_approval",
    event_ids: tuple[str, ...] = ("EV-1",),
    session_id: str = "SESS-0001",
) -> GroundTruthViolation:
    return GroundTruthViolation(
        violation_id="0" * 16,
        fault_class="V1",
        constraint_id=constraint_id,
        session_id=session_id,
        ocel_event_ids=event_ids,
        ocel_object_ids=("DEC-X",),
        expected_evidence_span_ids=(),
        removed_span_ids=(),
        severity="high",
        articles=("Reg (EU) 2024/1689 Art. 14",),
        description="test truth",
    )


def _pred(
    constraint_id: str = "T1.synchronised_approval",
    event_ids: tuple[str, ...] = ("EV-1",),
) -> Violation:
    return Violation(constraint_id=constraint_id, ocel_event_ids=event_ids)


class TestExactMatch:
    def test_requires_same_constraint_and_event_set(self) -> None:
        assert exact_match(_pred(), _truth())
        assert not exact_match(_pred(constraint_id="T2.mandatory_data_coverage"), _truth())
        assert not exact_match(_pred(event_ids=("EV-2",)), _truth())
        # Subset/superset is NOT a match: the anchors are pre-registered.
        assert not exact_match(_pred(event_ids=("EV-1", "EV-2")), _truth())

    def test_event_order_is_insensitive(self) -> None:
        truth = _truth(event_ids=("EV-2", "EV-1"))
        assert exact_match(_pred(event_ids=("EV-1", "EV-2")), truth)

    def test_object_ids_never_matter(self) -> None:
        prediction = Violation(
            constraint_id="T1.synchronised_approval",
            ocel_event_ids=("EV-1",),
            ocel_object_ids=("SOMETHING", "ELSE"),
        )
        assert exact_match(prediction, _truth())


class TestConfusion:
    def test_perfect_agreement(self) -> None:
        truths = (_truth(), _truth(constraint_id="T4.reason_code_presence", event_ids=("EV-9",)))
        predicted = [
            _pred(constraint_id="T4.reason_code_presence", event_ids=("EV-9",)),
            _pred(),
        ]
        assert confusion(predicted, truths) == ConfusionMatrix(
            true_positives=2, false_positives=0, false_negatives=0
        )

    def test_miss_and_false_alarm(self) -> None:
        truths = (_truth(), _truth(event_ids=("EV-7",), session_id="SESS-0002"))
        predicted = [_pred(), _pred(event_ids=("EV-8",))]
        assert confusion(predicted, truths) == ConfusionMatrix(
            true_positives=1, false_positives=1, false_negatives=1
        )

    def test_duplicate_predictions_count_as_false_positives(self) -> None:
        """Matching is 1:1 — a second identical prediction is a false alarm."""
        predicted = [_pred(), _pred()]
        assert confusion(predicted, (_truth(),)) == ConfusionMatrix(
            true_positives=1, false_positives=1, false_negatives=0
        )

    def test_empty_both_sides(self) -> None:
        assert confusion([], ()) == ConfusionMatrix(
            true_positives=0, false_positives=0, false_negatives=0
        )

    def test_custom_matcher_injection(self) -> None:
        """The seam the Phase 7/8 judge matcher plugs into."""

        def overlap(predicted: Violation, truth: GroundTruthViolation) -> bool:
            return predicted.constraint_id == truth.constraint_id and bool(
                set(predicted.ocel_event_ids) & set(truth.ocel_event_ids)
            )

        predicted = [_pred(event_ids=("EV-1", "EV-99"))]
        truths = (_truth(event_ids=("EV-1", "EV-2")),)
        assert confusion(predicted, truths).true_positives == 0  # exact: no
        assert confusion(predicted, truths, matcher=overlap).true_positives == 1


class TestPrecisionRecallF1:
    def test_perfect(self) -> None:
        assert precision_recall_f1(
            ConfusionMatrix(true_positives=8, false_positives=0, false_negatives=0)
        ) == (1.0, 1.0, 1.0)

    def test_clean_split_convention(self) -> None:
        """0 predicted, 0 truth is a perfect clean-split outcome, not 0/0."""
        assert precision_recall_f1(
            ConfusionMatrix(true_positives=0, false_positives=0, false_negatives=0)
        ) == (1.0, 1.0, 1.0)

    def test_all_missed(self) -> None:
        precision, recall, f1 = precision_recall_f1(
            ConfusionMatrix(true_positives=0, false_positives=0, false_negatives=3)
        )
        assert (precision, recall, f1) == (0.0, 0.0, 0.0)

    def test_all_false_alarms(self) -> None:
        precision, recall, f1 = precision_recall_f1(
            ConfusionMatrix(true_positives=0, false_positives=3, false_negatives=0)
        )
        assert (precision, recall, f1) == (0.0, 0.0, 0.0)

    def test_mixed(self) -> None:
        precision, recall, f1 = precision_recall_f1(
            ConfusionMatrix(true_positives=6, false_positives=2, false_negatives=2)
        )
        assert precision == 0.75
        assert recall == 0.75
        assert f1 == 0.75

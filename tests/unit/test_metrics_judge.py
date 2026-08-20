"""The judge matching function and determinism score (Phase 7, I4 freeze items).

`judge_matcher` is the pre-registered judge-to-ground-truth matching function
(constraint-class synonym map + event-id overlap threshold); `session_match`
is the separately-reported localisation-only partial credit. Both plug into
`confusion(matcher=...)` and never replace the frozen `exact_match` default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auditors_trace.constraints.templates import Violation
from auditors_trace.eval.metrics import (
    adjudication_queue,
    confusion,
    determinism_score,
    judge_matcher,
    load_judge_matching,
    session_match,
)
from auditors_trace.scenario.injector import GroundTruthViolation

REPO = Path(__file__).resolve().parent.parent.parent
MATCHING = REPO / "rules" / "judge_matching.yaml"
REQUIRED_IDS = frozenset(
    {
        "T1.synchronised_approval",
        "T2.mandatory_data_coverage",
        "T3.delegation_integrity",
        "T4.reason_code_presence",
        "T5.prohibited_attribute_access",
        "STD.policy_version_current",
    }
)


@pytest.fixture(scope="module")
def matching() -> object:
    return load_judge_matching(MATCHING, required_ids=REQUIRED_IDS)


def _truth(
    constraint_id: str = "T1.synchronised_approval",
    event_ids: tuple[str, ...] = ("EV-1", "EV-2"),
    session_id: str = "SESS-0001",
) -> GroundTruthViolation:
    return GroundTruthViolation(
        violation_id="0" * 16,
        fault_class="V1",
        constraint_id=constraint_id,
        session_id=session_id,
        ocel_event_ids=event_ids,
        ocel_object_ids=(),
        expected_evidence_span_ids=(),
        removed_span_ids=(),
        severity="high",
        articles=("Reg (EU) 2024/1689 Art. 14",),
        description="test truth",
    )


def _pred(
    constraint_id: str = "T1.synchronised_approval",
    event_ids: tuple[str, ...] = ("EV-1",),
    session_id: str = "SESS-0001",
) -> Violation:
    return Violation(constraint_id=constraint_id, ocel_event_ids=event_ids, session_id=session_id)


class TestDeterminismScore:
    def test_modal_frequency(self) -> None:
        agree = [_pred()]
        other_a = [_pred(constraint_id="T2.mandatory_data_coverage")]
        other_b = [_pred(event_ids=("EV-9",))]
        score = determinism_score([agree, agree, list(agree), other_a, other_b])
        assert score == 0.6

    def test_all_agree_is_one(self) -> None:
        runs = [[_pred()], [_pred()], [_pred()]]
        assert determinism_score(runs) == 1.0

    def test_order_within_a_set_does_not_matter(self) -> None:
        a = [_pred(), _pred(constraint_id="T4.reason_code_presence", event_ids=("EV-7",))]
        b = list(reversed(a))
        assert determinism_score([a, b]) == 1.0

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            determinism_score([])


class TestJudgeMatcher:
    def test_canonical_id_passes_case_insensitively(self, matching: object) -> None:
        match = judge_matcher(matching)  # type: ignore[arg-type]
        assert match(_pred(), _truth())
        assert match(_pred(constraint_id="t1.synchronised_approval"), _truth())

    def test_synonym_normalisation(self, matching: object) -> None:
        match = judge_matcher(matching)  # type: ignore[arg-type]
        assert match(_pred(constraint_id="missing human approval"), _truth())
        assert match(_pred(constraint_id="Missing Human Approval"), _truth())
        assert not match(_pred(constraint_id="entirely unknown label"), _truth())

    def test_overlap_threshold_boundaries(self, matching: object) -> None:
        match = judge_matcher(matching)  # type: ignore[arg-type]
        assert match(_pred(event_ids=("EV-1", "EV-99")), _truth())  # overlap 1 >= 1
        assert not match(_pred(event_ids=("EV-98", "EV-99")), _truth())  # overlap 0

    def test_wrong_class_with_full_overlap_fails(self, matching: object) -> None:
        match = judge_matcher(matching)  # type: ignore[arg-type]
        assert not match(
            _pred(constraint_id="T2.mandatory_data_coverage", event_ids=("EV-1", "EV-2")),
            _truth(),
        )

    def test_plugs_into_confusion(self, matching: object) -> None:
        match = judge_matcher(matching)  # type: ignore[arg-type]
        matrix = confusion(
            [_pred(constraint_id="missing human approval")], (_truth(),), matcher=match
        )
        assert matrix.true_positives == 1


class TestSessionMatch:
    def test_same_session_matches(self) -> None:
        assert session_match(_pred(event_ids=("EV-77",)), _truth())

    def test_different_session_fails(self) -> None:
        assert not session_match(_pred(session_id="SESS-0002"), _truth())

    def test_empty_session_never_matches(self) -> None:
        assert not session_match(_pred(session_id=""), _truth(session_id=""))


class TestLoader:
    def test_shipped_matching_loads(self, matching: object) -> None:
        assert matching.overlap_threshold >= 1  # type: ignore[attr-defined]
        assert matching.synonyms  # type: ignore[attr-defined]

    def test_placeholder_synonym_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "matching.yaml"
        path.write_text(
            'schema_version: 1\nmatching_version: "x"\noverlap_threshold: 1\n'
            'session_credit: same_session\nsynonyms:\n  "TODO fill": T1.synchronised_approval\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="placeholder"):
            load_judge_matching(path, required_ids=REQUIRED_IDS)

    def test_unknown_constraint_id_value_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "matching.yaml"
        path.write_text(
            'schema_version: 1\nmatching_version: "x"\noverlap_threshold: 1\n'
            'session_credit: same_session\nsynonyms:\n  "some label": T9.heldout\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="T9"):
            load_judge_matching(path, required_ids=REQUIRED_IDS)

    def test_duplicate_yaml_keys_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "matching.yaml"
        path.write_text(
            'schema_version: 1\nmatching_version: "x"\nmatching_version: "y"\n'
            "overlap_threshold: 1\nsession_credit: same_session\nsynonyms: {}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_judge_matching(path, required_ids=REQUIRED_IDS)


class TestAdjudicationQueue:
    def test_unmatched_only_in_canonical_order(self, matching: object) -> None:
        matched = _pred(constraint_id="missing human approval")
        stray_b = _pred(constraint_id="T4.reason_code_presence", event_ids=("EV-50",))
        stray_a = _pred(constraint_id="T2.mandatory_data_coverage", event_ids=("EV-40",))
        queue = adjudication_queue(
            [matched, stray_b, stray_a],
            (_truth(),),
            matching=matching,  # type: ignore[arg-type]
        )
        assert [violation.constraint_id for violation in queue] == [
            "T2.mandatory_data_coverage",
            "T4.reason_code_presence",
        ]

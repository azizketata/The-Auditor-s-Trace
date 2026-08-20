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
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auditors_trace.constraints.ruleset import (
    CONSTRAINT_ID_PATTERN,
    StrictKeyLoader,
    substantive_text,
)
from auditors_trace.constraints.templates import Violation, violation_sort_key
from auditors_trace.scenario.injector import GroundTruthViolation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

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


def verdict_set_key(violations: Sequence[Violation]) -> str:
    """Canonical equality key for a whole verdict set.

    Order-insensitive over the violations, keyed by the ground-truth join
    fields only (constraint id + sorted event ids) — severity/detail/session
    never affect whether two runs 'agree'. Shared by
    :func:`determinism_score` and the judge's modal-verdict aggregator so
    the two can never disagree about what 'the same verdict set' means.
    """
    from auditors_trace.model.log import canonical_json

    return canonical_json(
        sorted(
            (violation.constraint_id, sorted(violation.ocel_event_ids)) for violation in violations
        )
    )


def determinism_score(verdict_sets: list[list[Violation]]) -> float:
    """D = runs producing the modal verdict set, over total runs.

    Expected to be 1.0 for the engine and below 1.0 for the judge baseline.
    """
    if not verdict_sets:
        raise ValueError("determinism_score needs at least one run; got an empty list")
    counts: dict[str, int] = {}
    for verdict_set in verdict_sets:
        key = verdict_set_key(verdict_set)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.values()) / len(verdict_sets)


class JudgeMatching(BaseModel):
    """The pre-registered judge matching parameters (I4 freeze item)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    matching_version: str = Field(min_length=1)
    overlap_threshold: int = Field(ge=1)
    session_credit: Literal["same_session"]
    synonyms: dict[str, str]

    @field_validator("matching_version")
    @classmethod
    def _substantive_version(cls, value: str) -> str:
        return substantive_text(value)

    @field_validator("synonyms")
    @classmethod
    def _synonyms_shape(cls, value: dict[str, str]) -> dict[str, str]:
        import re

        for label, constraint_id in value.items():
            substantive_text(label)
            if label != label.lower():
                raise ValueError(f"synonym label {label!r} must be lowercase")
            if not re.match(CONSTRAINT_ID_PATTERN, constraint_id):
                raise ValueError(f"synonym target {constraint_id!r} is not a constraint id")
        return value


def load_judge_matching(path: Path, *, required_ids: Iterable[str]) -> JudgeMatching:
    """Load and validate the judge matching parameters.

    Every synonym target must be one of ``required_ids`` (the ruleset's
    closed vocabulary) — a synonym pointing at a constraint the engine does
    not know is a load-time error.
    """
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.load(text, Loader=StrictKeyLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: top level is not a mapping")
    matching = JudgeMatching.model_validate(raw)
    known = frozenset(required_ids)
    unknown = sorted({cid for cid in matching.synonyms.values() if cid not in known})
    if unknown:
        raise ValueError(
            f"{path.name}: synonym targets outside the ruleset vocabulary: {', '.join(unknown)}"
        )
    return matching


def _normalise_constraint_label(label: str, matching: JudgeMatching) -> str:
    lowered = label.strip().lower()
    if lowered in matching.synonyms:
        return matching.synonyms[lowered]
    return lowered


def judge_matcher(matching: JudgeMatching) -> Matcher:
    """The pre-registered judge-to-ground-truth matcher (I4 freeze item).

    A prediction matches a truth iff its constraint label — normalised
    through the synonym map, with canonical ids passing through
    case-insensitively — equals the truth's constraint id, AND the cited
    event ids overlap the truth's anchors by at least the threshold.
    Plugs into :func:`confusion`'s ``matcher`` seam; never replaces the
    frozen ``exact_match`` default.
    """

    def match(predicted: Violation, truth: GroundTruthViolation) -> bool:
        normalised = _normalise_constraint_label(predicted.constraint_id, matching)
        if normalised.lower() != truth.constraint_id.lower():
            return False
        overlap = set(predicted.ocel_event_ids) & set(truth.ocel_event_ids)
        return len(overlap) >= matching.overlap_threshold

    return match


def judge_match(
    predicted: Violation, truth: GroundTruthViolation, *, matching: JudgeMatching
) -> bool:
    """Convenience form of :func:`judge_matcher` for single comparisons."""
    return judge_matcher(matching)(predicted, truth)


def session_match(predicted: Violation, truth: GroundTruthViolation) -> bool:
    """Localisation-only partial credit: same non-empty session.

    Reported SEPARATELY, never as the headline metric. It is the ONLY
    primary metric for the flattened-DECLARE and OC-DFG baselines: their
    deviations carry no pre-registered anchors and no constraint-class
    semantics, so ``exact_match`` (and even ``judge_matcher``) would be
    structurally unwinnable for them — session credit measures the one thing
    they can honestly claim, localisation.
    """
    return predicted.session_id != "" and predicted.session_id == truth.session_id


def adjudication_queue(
    predicted: list[Violation],
    truth: tuple[GroundTruthViolation, ...],
    *,
    matching: JudgeMatching,
) -> tuple[Violation, ...]:
    """Judge claims matching NO ground truth, in canonical order.

    The structured input to the blind adjudication step (PLAN-REVIEW B2):
    a human adjudicates each against the article text before it counts as a
    definitive false positive. Until then, counting treats these as false
    positives — the honest default.
    """
    match = judge_matcher(matching)
    unmatched = [
        violation
        for violation in predicted
        if not any(match(violation, ground_truth) for ground_truth in truth)
    ]
    return tuple(sorted(unmatched, key=violation_sort_key))


def evidence_reproducibility(record_hashes: list[list[str]]) -> float:
    """Fraction of records whose hash is identical across every run."""
    raise NotImplementedError

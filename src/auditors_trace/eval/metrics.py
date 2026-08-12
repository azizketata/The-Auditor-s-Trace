"""Detection, determinism, reproducibility, and scalability metrics.

Phase 8 deliverable.
"""

from __future__ import annotations

from dataclasses import dataclass

from auditors_trace.constraints.templates import Violation
from auditors_trace.scenario.injector import GroundTruthViolation


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    """True positives, false positives, false negatives."""


def confusion(
    predicted: list[Violation], truth: tuple[GroundTruthViolation, ...]
) -> ConfusionMatrix:
    """Match predictions against ground truth by (constraint id, event ids)."""
    raise NotImplementedError


def precision_recall_f1(matrix: ConfusionMatrix) -> tuple[float, float, float]:
    """Return precision, recall, and F1."""
    raise NotImplementedError


def determinism_score(verdict_sets: list[list[Violation]]) -> float:
    """D = runs producing the modal verdict set, over total runs.

    Expected to be 1.0 for the engine and below 1.0 for the judge baseline.
    """
    raise NotImplementedError


def evidence_reproducibility(record_hashes: list[list[str]]) -> float:
    """Fraction of records whose hash is identical across every run."""
    raise NotImplementedError

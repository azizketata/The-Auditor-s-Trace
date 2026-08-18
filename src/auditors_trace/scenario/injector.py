"""Violation injection and the pre-registered ground-truth catalogue.

Phase 4 deliverable. Injection operates at SPAN level (a pure seeded function
over the span JSONL files), after which the faulted spans are re-mapped
through the Phase 3 mapper — so every downstream system consumes artifacts
derived from the same faulted telemetry, and evidence records cite span ids
that actually exhibit the fault. (Amended 13 Aug 2026, docs/PLAN-REVIEW.md
B1: the original OCEL-level design made the LLM-judge baseline structurally
blind to the injected faults.)

Invariant I4: ``data/catalogue/violations.yaml`` is frozen, git-tagged,
pushed, and externally timestamped before any detection experiment runs.
Never edit it after the tag. If it is wrong, stop and report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from auditors_trace.model.log import OCELLog

Split = Literal["clean", "single", "mixed"]


@dataclass(frozen=True, slots=True)
class GroundTruthViolation:
    """One injected fault: its id, the events it touched, and the constraint it breaks."""


def load_catalogue(path: Path) -> tuple[GroundTruthViolation, ...]:
    """Load the catalogue. Raises if any entry lacks an article reference."""
    raise NotImplementedError


def catalogue_hash(path: Path) -> str:
    """Return the catalogue's sha256, checked against the ``catalogue-v1`` tag."""
    raise NotImplementedError


def inject(
    log: OCELLog, split: Split, seed: int
) -> tuple[OCELLog, tuple[GroundTruthViolation, ...]]:
    """Produce a labelled log. The ``clean`` split injects nothing, by construction."""
    raise NotImplementedError

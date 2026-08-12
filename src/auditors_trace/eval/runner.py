"""Run every system over every split and write `results/`.

Phase 8 deliverable. Satisfies invariant I5: ``make all`` reproduces every number
and figure in the paper from scratch, and ``results/expected.json`` must match
exactly on a rerun.
"""

from __future__ import annotations

from pathlib import Path


def run_all(results_dir: Path) -> Path:
    """Run engine and baselines over all splits. Returns the results manifest path."""
    raise NotImplementedError


def compare_to_expected(results_dir: Path, expected: Path) -> bool:
    """Return whether a rerun reproduces the committed results exactly."""
    raise NotImplementedError

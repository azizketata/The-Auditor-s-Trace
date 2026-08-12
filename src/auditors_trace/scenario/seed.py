"""Statlog German Credit loader.

Phase 2 deliverable. UCI dataset id 144, CC BY 4.0. Downloaded at build time
into ``data/seed/``, never committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Applicant:
    """One pseudonymous applicant record. Fields land in Phase 2."""


def download_seed(target: Path) -> Path:
    """Download the German Credit dataset if absent. Returns the local path."""
    raise NotImplementedError


def load_applicants(path: Path, count: int, seed: int) -> tuple[Applicant, ...]:
    """Draw a reproducible applicant sequence. The same seed yields the same order."""
    raise NotImplementedError

"""Map `gen_ai.*` and `openinference.*` / `llm.*` attributes onto OCEL attributes.

Phase 3 deliverable. Both vocabularies must produce identical OCEL output: the
GenAI semantic conventions are still experimental and instrumentation varies.

Unmapped attributes are recorded in the coverage report, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Which governance-relevant attributes were mapped, and which were not.

    Serialised to ``results/e2_coverage.json``; the Phase 3 gate requires the
    mapped fraction to exceed 0.9. This is the evidence for claim C2.
    """


def normalise_attributes(raw: dict[str, Any]) -> dict[str, Any]:
    """Fold either vocabulary onto one canonical attribute namespace."""
    raise NotImplementedError


def coverage(mapped: dict[str, Any], raw: dict[str, Any]) -> CoverageReport:
    """Report which governance-relevant attributes survived normalisation."""
    raise NotImplementedError

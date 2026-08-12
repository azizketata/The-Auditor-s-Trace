"""Load and validate `rules/crosswalk.yaml`, the article and standard mapping.

Phase 6 deliverable, but only the loader and the validator. The crosswalk
*content* is Alina's deliverable (section 11); the harness must never generate
it. Articles 12, 14, 26, 72 plus ISO/IEC 24970, ISO/IEC 42001, NIST AI RMF.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from auditors_trace.evidence.record import LegalBasis, StandardRef


class Crosswalk(BaseModel):
    """Constraint id to legal basis and standard references."""


def load_crosswalk(path: Path) -> Crosswalk:
    """Load and validate the crosswalk. Raises on any unmapped constraint id."""
    raise NotImplementedError


def legal_basis_for(crosswalk: Crosswalk, constraint_id: str) -> list[LegalBasis]:
    """Return the legal bases for a constraint. Never empty; raises if unmapped."""
    raise NotImplementedError


def standard_refs_for(crosswalk: Crosswalk, constraint_id: str) -> list[StandardRef]:
    """Return the standard and framework references for a constraint."""
    raise NotImplementedError

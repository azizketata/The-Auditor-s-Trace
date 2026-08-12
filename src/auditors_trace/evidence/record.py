"""The evidence record schema.

Phase 6 deliverable. Builds to BUILD-PLAN.md section 8 exactly.

``generated_at`` may exist as a field but is excluded from every hash: no
wall-clock value enters any hashed payload (invariant I1).
"""

from __future__ import annotations

from pydantic import BaseModel


class LegalBasis(BaseModel):
    """Instrument, article, paragraph, and the requirement text it imposes."""


class StandardRef(BaseModel):
    """A reference into ISO/IEC 24970, ISO/IEC 42001, or the NIST AI RMF."""


class EvidenceLocator(BaseModel):
    """The OCEL event/object ids and OTel trace/span ids the violation rests on."""


class Provenance(BaseModel):
    """Engine version, engine commit, and the input log hash."""


class Reproducibility(BaseModel):
    """The rerun command and the record hash it must reproduce."""


class Integrity(BaseModel):
    """Chain index, this record's hash, and the previous record's hash."""


class Retention(BaseModel):
    """Retention class and minimum period, per Article 26(6)."""


class EvidenceRecord(BaseModel):
    """One violation rendered as defensible, reproducible audit evidence."""

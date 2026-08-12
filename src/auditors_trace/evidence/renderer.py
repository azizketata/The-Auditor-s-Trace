"""Violation to evidence record.

Phase 6 deliverable. Satisfies the technical half of claim C4 (each violation
renders into a defensible, reproducible evidence record).

No LLM call, ever. The natural-language constraint statement comes from the
ruleset and the crosswalk, both authored by hand.
"""

from __future__ import annotations

from auditors_trace.constraints.templates import Violation
from auditors_trace.evidence.crosswalk import Crosswalk
from auditors_trace.evidence.record import EvidenceRecord


def render(
    violation: Violation,
    crosswalk: Crosswalk,
    ruleset_version: str,
    input_log_sha256: str,
) -> EvidenceRecord:
    """Render one violation as an unchained evidence record."""
    raise NotImplementedError


def render_all(
    violations: list[Violation],
    crosswalk: Crosswalk,
    ruleset_version: str,
    input_log_sha256: str,
) -> list[EvidenceRecord]:
    """Render and chain a violation set into an ordered evidence log."""
    raise NotImplementedError

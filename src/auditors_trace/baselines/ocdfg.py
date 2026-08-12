"""Object-centric directly-follows graph baseline.

Phase 7 deliverable. pm4py's OC-DFG conformance: the non-declarative
object-centric comparison.
"""

from __future__ import annotations

from auditors_trace.constraints.templates import Violation
from auditors_trace.model.log import OCELLog


def detect(log: OCELLog) -> list[Violation]:
    """Run OC-DFG conformance and return deviations in the shared violation format."""
    raise NotImplementedError

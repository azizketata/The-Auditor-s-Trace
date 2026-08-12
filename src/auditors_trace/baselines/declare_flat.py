"""Case-centric DECLARE baseline.

Phase 7 deliverable. Flatten the OCEL log to XES on the ``Application`` object,
then run pm4py's declarative conformance.

The point is not to win: it is to show which violations become invisible after
flattening. V2 and V7 are the expected casualties, and that is the E1 result.
"""

from __future__ import annotations

from auditors_trace.constraints.templates import Violation
from auditors_trace.model.log import OCELLog


def flatten_to_xes(log: OCELLog, anchor_object_type: str) -> object:
    """Flatten an OCEL log onto one object type, producing a pm4py event log."""
    raise NotImplementedError


def detect(log: OCELLog) -> list[Violation]:
    """Run case-centric declarative conformance on the flattened log."""
    raise NotImplementedError

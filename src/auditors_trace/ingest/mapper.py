"""Span tree to OCEL 2.0 events, objects, and qualified relations.

Phase 3 deliverable. Satisfies claim C2 (the mapping is lossless for governance
attributes).

Determinism: walk depth-first, children sorted by (start time, span id). Object
ids derive from span attributes, never from generation order.
"""

from __future__ import annotations

from auditors_trace.ingest.attribute_map import CoverageReport
from auditors_trace.ingest.otel_reader import SpanTree
from auditors_trace.model.log import OCELLog


def map_span_trees(trees: tuple[SpanTree, ...]) -> tuple[OCELLog, CoverageReport]:
    """Convert span trees into one OCEL 2.0 log plus an attribute coverage report."""
    raise NotImplementedError

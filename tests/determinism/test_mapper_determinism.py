"""Phase 3 determinism: ten pipelines, one hash, one byte-string each.

Runs from file bytes each time (fresh read, fresh trees, fresh map) so any
hidden iteration-order or environment dependence shows up as a second
distinct artefact. CI runs this on Windows and Ubuntu; the pinned golden
hashes in tests/integration/test_mapper_golden.py close the cross-platform
loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auditors_trace.ingest.attribute_map import coverage_json
from auditors_trace.ingest.mapper import map_span_trees, span_index_json
from auditors_trace.ingest.otel_reader import build_span_trees, read_spans
from auditors_trace.model.log import log_hash

SPANS = Path(__file__).resolve().parent.parent / "golden" / "spans"


@pytest.mark.parametrize(
    "spans_name",
    ["credit_grant_approval_seed42.jsonl", "paired_vocabulary_genai.jsonl"],
    ids=["grant", "paired"],
)
def test_mapper_is_deterministic(spans_name: str) -> None:
    hashes: set[str] = set()
    coverage_bytes: set[str] = set()
    index_bytes: set[str] = set()
    for _ in range(10):
        run = map_span_trees(build_span_trees(read_spans(SPANS / spans_name)))
        hashes.add(log_hash(run.log))
        coverage_bytes.add(coverage_json(run.coverage, None))
        index_bytes.add(span_index_json(run.span_index))
    assert len(hashes) == 1
    assert len(coverage_bytes) == 1
    assert len(index_bytes) == 1

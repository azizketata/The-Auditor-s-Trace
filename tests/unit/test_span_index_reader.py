"""The span-index sidecar reader (Phase 6 renderer input).

The writer (`span_index_json`) existed since Phase 3; evidence records need
the way back. The reader is a strict mirror: exact key sets, pinned schema
version, unique non-empty ids — a sidecar that drifted from the writer's
shape must fail loudly, never render wrong span citations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auditors_trace.ingest.mapper import (
    EventSpanRef,
    SessionSpanRef,
    SpanIndex,
    read_span_index,
    span_index_json,
)
from auditors_trace.ingest.otel_reader import IngestError

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "mapper"


def _index() -> SpanIndex:
    return SpanIndex(
        schema_version=1,
        sessions=(
            SessionSpanRef(
                session_id="SESS-0000",
                trace_id="5f4d7e4c8991a7b690adaf603e2e12bf",
                root_span_id="109a6b43bac432ec",
                session_span_ids=("93c6c228d3b4a932",),
            ),
        ),
        events=(
            EventSpanRef(
                event_id="EVT-SESS-0000-001-session_start",
                trace_id="5f4d7e4c8991a7b690adaf603e2e12bf",
                span_id="3798dbdac2ca5d97",
                enrichment_span_ids=("f75df6a0a5df9861",),
            ),
        ),
    )


def _write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "index.span_index.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _payload() -> dict[str, Any]:
    return json.loads(span_index_json(_index()))


def test_round_trip_through_the_writer(tmp_path: Path) -> None:
    path = tmp_path / "index.span_index.json"
    path.write_text(span_index_json(_index()), encoding="utf-8", newline="\n")
    assert read_span_index(path) == _index()


def test_committed_goldens_round_trip() -> None:
    golden_files = sorted(GOLDEN.glob("*.span_index.json"))
    assert golden_files, "mapper goldens missing"
    for path in golden_files:
        index = read_span_index(path)
        assert span_index_json(index).encode("utf-8") == path.read_bytes(), path.name


def test_schema_version_pinned(tmp_path: Path) -> None:
    payload = _payload()
    payload["schema_version"] = 2
    with pytest.raises(IngestError, match="schema_version"):
        read_span_index(_write(tmp_path, payload))
    payload["schema_version"] = True  # bool is not an int here
    with pytest.raises(IngestError, match="schema_version"):
        read_span_index(_write(tmp_path, payload))


def test_exact_key_sets_enforced(tmp_path: Path) -> None:
    payload = _payload()
    payload["surprise"] = 1
    with pytest.raises(IngestError):
        read_span_index(_write(tmp_path, payload))

    payload = _payload()
    del payload["sessions"]
    with pytest.raises(IngestError):
        read_span_index(_write(tmp_path, payload))

    payload = _payload()
    payload["events"][0]["surprise"] = 1
    with pytest.raises(IngestError):
        read_span_index(_write(tmp_path, payload))


def test_ids_must_be_non_empty_strings(tmp_path: Path) -> None:
    payload = _payload()
    payload["events"][0]["span_id"] = ""
    with pytest.raises(IngestError):
        read_span_index(_write(tmp_path, payload))

    payload = _payload()
    payload["sessions"][0]["session_span_ids"] = [7]
    with pytest.raises(IngestError):
        read_span_index(_write(tmp_path, payload))


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    payload = _payload()
    payload["events"].append(payload["events"][0])
    with pytest.raises(IngestError, match="duplicate"):
        read_span_index(_write(tmp_path, payload))


def test_invalid_json_rejected(tmp_path: Path) -> None:
    path = tmp_path / "index.span_index.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(IngestError):
        read_span_index(path)

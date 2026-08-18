"""Shared fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

#: The only fields that may legitimately differ between two identical scripted
#: runs: span wall-clock nanoseconds (real telemetry) and the LangGraph task
#: UUIDs inside the OpenInference metadata blob. Everything else is
#: byte-stable, and both the determinism suite and the golden-regeneration
#: test normalise exactly this set — nothing more.
VOLATILE_METADATA_KEYS = ("langgraph_checkpoint_ns", "checkpoint_ns")


def normalise_span_line(line: str) -> str:
    """Canonicalise one span-file line with the volatile fields zeroed."""
    row = json.loads(line)
    row["start_time_unix_nano"] = 0
    row["end_time_unix_nano"] = 0
    for event in row.get("events", []):
        event["timestamp_unix_nano"] = 0
    metadata = row["attributes"].get("metadata")
    if isinstance(metadata, str):
        # Preserve the blob's own key order (no sort_keys): only the volatile
        # keys are dropped, so an order-level difference is still caught.
        blob = json.loads(metadata)
        for key in VOLATILE_METADATA_KEYS:
            blob.pop(key, None)
        row["attributes"]["metadata"] = json.dumps(blob)
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pytest.fixture(scope="session")
def span_line_normaliser() -> Callable[[str], str]:
    """The one span-line normaliser, shared by every suite that compares runs."""
    return normalise_span_line


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The repository root, resolved from this file's location."""
    return Path(__file__).resolve().parent.parent

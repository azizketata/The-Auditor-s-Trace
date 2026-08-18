"""The committed golden span fixtures stay parseable — and the messy one
stays *deliberately* broken in exactly the way Phase 3 must survive."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auditors_trace.model.ocel_schema import EventType
from auditors_trace.model.span_contract import SpanContractError, parse_event, parse_scope

SPANS = Path(__file__).resolve().parent / "spans"


def _rows(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (SPANS / name).read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("filename", "approval_event"),
    [
        ("credit_grant_approval_seed42.jsonl", EventType.GRANT_APPROVAL),
        ("credit_deny_approval_refer_seed2.jsonl", EventType.DENY_APPROVAL),
    ],
)
def test_generated_goldens_parse_cleanly(filename: str, approval_event: EventType) -> None:
    events = []
    for row in _rows(filename):
        spec = parse_event(row["attributes"])
        if spec is not None:
            events.append(spec)
    assert len(events) == 28
    assert sorted(e.seq for e in events) == list(range(1, 29))
    assert any(e.event_type is approval_event for e in events)


def test_deny_golden_ends_in_refer() -> None:
    events = [
        spec
        for row in _rows("credit_deny_approval_refer_seed2.jsonl")
        if (spec := parse_event(row["attributes"])) is not None
    ]
    decision = next(e for e in events if e.event_type is EventType.MAKE_DECISION)
    assert dict(decision.attributes)["outcome"] == "refer"


def test_messy_fixture_layer_a_lines_behave_as_documented() -> None:
    rows = _rows("messy_vendor_variants.jsonl")
    assert len(rows) == 6
    parsed, malformed, non_events = 0, 0, 0
    for row in rows:
        attrs = row["attributes"]
        if parse_scope(attrs) is not None and "at.event.type" in attrs:
            try:
                assert parse_event(attrs) is not None
                parsed += 1
            except SpanContractError:
                malformed += 1
        else:
            assert parse_event(attrs) is None  # non-events must return None
            non_events += 1
    assert parsed == 1  # the valid minimal layer-A event
    assert malformed == 1  # the deliberate at.obj.count mismatch
    assert non_events == 4  # root + gen_ai-only + partial + renamed-vendor

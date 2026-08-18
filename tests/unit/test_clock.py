"""The simulated clock is total, monotonic, and policy-consistent."""

from __future__ import annotations

from datetime import datetime

import pytest

from auditors_trace.model.ocel_schema import EventType
from auditors_trace.scenario.catalogue import PolicyVersionSpec
from auditors_trace.scenario.clock import (
    EVENT_DURATION_SECONDS,
    SESSION_STRIDE_SECONDS,
    assert_within_policy,
    session_clock,
)

CURRENT = PolicyVersionSpec("POL-CREDIT", "2026.02", "2026-02-01T00:00:00Z", "", True)
SUPERSEDED = PolicyVersionSpec(
    "POL-CREDIT", "2025.11", "2025-11-01T00:00:00Z", "2026-01-31T23:59:59Z", False
)


def test_every_event_type_has_a_duration() -> None:
    assert set(EVENT_DURATION_SECONDS) == set(EventType)
    assert all(d >= 1 for d in EVENT_DURATION_SECONDS.values())


def test_stamps_are_strictly_increasing() -> None:
    clock = session_clock(0)
    stamps = [clock.stamp(t) for t in EventType]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


def test_approval_strictly_precedes_decision() -> None:
    # The property template T1 checks, which the 15.625 ms Windows wall clock
    # cannot guarantee.
    clock = session_clock(3)
    approval = clock.stamp(EventType.GRANT_APPROVAL)
    decision = clock.stamp(EventType.MAKE_DECISION)
    assert datetime.fromisoformat(approval) < datetime.fromisoformat(decision)


def test_sessions_are_strided() -> None:
    delta = session_clock(1).session_start - session_clock(0).session_start
    assert delta.total_seconds() == SESSION_STRIDE_SECONDS


def test_peek_matches_next_stamp_and_does_not_advance() -> None:
    clock = session_clock(0)
    peeked = clock.peek(EventType.GRANT_APPROVAL)
    assert clock.peek(EventType.GRANT_APPROVAL) == peeked  # no advance
    assert clock.stamp(EventType.GRANT_APPROVAL) == peeked


def test_sessions_fall_inside_the_current_policy_window() -> None:
    for index in (0, 4999):  # the Phase 8 scalability run's extremes
        assert_within_policy(session_clock(index), CURRENT)


def test_session_outside_policy_window_fails_loudly() -> None:
    with pytest.raises(ValueError, match="postdates"):
        assert_within_policy(session_clock(0), SUPERSEDED)

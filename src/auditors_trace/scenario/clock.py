"""The deterministic simulated business clock supplying ``at.event.time``.

Why this exists: ``time.time_ns()`` resolves to 15.625 ms on Windows — twenty
consecutive calls return identical values, and sibling spans routinely tie on
start time. Template T1 requires ``grant_approval.timestamp <
make_decision.timestamp`` and V8 compares against PolicyVersion windows; with
wall clock, ordering would fall through to a random span-id tiebreak and the
OCEL log (and the Phase 5/6 hash chain) would differ on every run.

So: span start/end nanoseconds remain real telemetry, but the OCEL event
timestamp is this clock's output. Session *i* starts at ``EPOCH + i * 900 s``
and every event advances the cursor by a fixed per-event-type duration —
approvals take two simulated minutes, so they strictly precede the decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from auditors_trace.model.ocel_schema import EventType
from auditors_trace.scenario.catalogue import PolicyVersionSpec

EPOCH_ISO: Final[str] = "2026-03-02T09:00:00+00:00"
SESSION_STRIDE_SECONDS: Final[int] = 900

EVENT_DURATION_SECONDS: Final[Mapping[EventType, int]] = {
    EventType.SESSION_START: 1,
    EventType.INVOKE_AGENT: 1,
    EventType.HANDOFF: 1,
    EventType.CALL_LLM: 3,
    EventType.CALL_TOOL: 2,
    EventType.RETRIEVE_DATA: 2,
    EventType.EMIT_REASONING: 1,
    EventType.REQUEST_APPROVAL: 5,
    EventType.GRANT_APPROVAL: 120,
    EventType.DENY_APPROVAL: 120,
    EventType.MAKE_DECISION: 2,
    EventType.NOTIFY_APPLICANT: 1,
    EventType.SESSION_END: 1,
}


def _iso_z(dt: datetime) -> str:
    """Render a UTC datetime as ISO-8601 with a ``Z`` suffix."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class SimClock:
    """A per-session cursor over simulated business time."""

    session_start: datetime
    cursor: datetime

    def stamp(self, event_type: EventType) -> str:
        """Advance by the event type's duration and return the new timestamp."""
        self.cursor = self.cursor + timedelta(seconds=EVENT_DURATION_SECONDS[event_type])
        return _iso_z(self.cursor)

    def peek(self, event_type: EventType) -> str:
        """The timestamp :meth:`stamp` would return next, without advancing.

        Used when an event's *object attributes* (e.g. an Approval's
        ``decided_at``) must equal the event's own timestamp, which is only
        allocated when the event span opens.
        """
        return _iso_z(self.cursor + timedelta(seconds=EVENT_DURATION_SECONDS[event_type]))

    def elapsed_seconds(self) -> int:
        """Simulated seconds since the session started."""
        return int((self.cursor - self.session_start).total_seconds())


def session_clock(
    session_index: int,
    epoch_iso: str = EPOCH_ISO,
    stride_seconds: int = SESSION_STRIDE_SECONDS,
) -> SimClock:
    """The clock for session ``i``, starting at ``EPOCH + i * stride``."""
    epoch = datetime.fromisoformat(epoch_iso)
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=UTC)
    start = epoch + timedelta(seconds=session_index * stride_seconds)
    return SimClock(session_start=start, cursor=start)


def assert_within_policy(clock: SimClock, policy: PolicyVersionSpec) -> None:
    """Fail the run if a session falls outside the current policy's window.

    At 5000 sessions the run spans about 52 simulated days — comfortably inside
    an open-ended ``effective_to`` — but this assertion means a future stride
    change cannot silently corrupt V8's ground truth.
    """
    start = datetime.fromisoformat(policy.effective_from)
    if clock.session_start < start:
        raise ValueError(
            f"session at {clock.session_start.isoformat()} predates policy "
            f"{policy.object_id} (effective from {policy.effective_from})"
        )
    if policy.effective_to:
        end = datetime.fromisoformat(policy.effective_to)
        if clock.session_start > end:
            raise ValueError(
                f"session at {clock.session_start.isoformat()} postdates policy "
                f"{policy.object_id} (effective to {policy.effective_to})"
            )

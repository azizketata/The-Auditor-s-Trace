"""In-memory OCEL 2.0 log.

Phase 1 deliverable. Holds sorted, immutable collections; every accessor
returns a deterministic order. Nothing here may consult wall-clock time.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OCELObject:
    """An OCEL object. Fields land in Phase 1 (see section 5)."""


@dataclass(frozen=True, slots=True)
class OCELEvent:
    """An OCEL event with its qualified object relations. Fields land in Phase 1."""


@dataclass(frozen=True, slots=True)
class OCELLog:
    """An OCEL 2.0 log: objects, events, qualified relations, O2O relations."""


def events_sorted(log: OCELLog) -> tuple[OCELEvent, ...]:
    """Return every event in deterministic order: (timestamp, event id)."""
    raise NotImplementedError


def objects_sorted(log: OCELLog) -> tuple[OCELObject, ...]:
    """Return every object in deterministic order: (object type, object id)."""
    raise NotImplementedError


def log_hash(log: OCELLog) -> str:
    """Return the canonical sha256 of the log content, excluding file metadata.

    Stable across runs and across serialisation formats. This is the
    ``input_log_sha256`` that every evidence record cites.
    """
    raise NotImplementedError

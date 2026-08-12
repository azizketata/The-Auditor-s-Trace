"""Read and write OCEL 2.0 via pm4py, in all three serialisations.

Phase 1 deliverable. The Phase 1 gate is whether qualified relations survive a
pm4py round trip; if they do not, section 12's fallback is our own JSON-only
serialiser. Verify, never assume.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from auditors_trace.model.log import OCELLog

Serialisation = Literal["json", "xml", "sqlite"]


def read_ocel(path: Path, serialisation: Serialisation) -> OCELLog:
    """Read an OCEL 2.0 log from disk into the in-memory model."""
    raise NotImplementedError


def write_ocel(log: OCELLog, path: Path, serialisation: Serialisation) -> None:
    """Write an OCEL 2.0 log to disk, deterministically."""
    raise NotImplementedError


def detect_serialisation(path: Path) -> Serialisation:
    """Infer the serialisation from a path suffix."""
    raise NotImplementedError

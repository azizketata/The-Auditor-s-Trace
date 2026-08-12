"""Canonical serialisation and hash chaining.

Phase 6 deliverable. The foundation of invariant I1: canonical JSON is
``json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)``
and nothing else. ``prev_record_sha256`` of the first record is 64 zeros.
"""

from __future__ import annotations

from typing import Any

from auditors_trace.evidence.record import EvidenceRecord

GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> str:
    """Serialise deterministically: sorted keys, fixed separators, no ASCII escaping."""
    raise NotImplementedError


def sha256_hex(payload: str) -> str:
    """Return the hex sha256 of a UTF-8 encoded payload."""
    raise NotImplementedError


def record_hash(record: EvidenceRecord) -> str:
    """Hash every field except ``integrity``, and never ``generated_at``."""
    raise NotImplementedError


def chain(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Sort records deterministically and link each to its predecessor's hash."""
    raise NotImplementedError


def verify_chain(records: list[EvidenceRecord]) -> bool:
    """Return whether every link holds. Any mutated record breaks verification."""
    raise NotImplementedError

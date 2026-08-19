"""Canonical serialisation and hash chaining.

Phase 6 deliverable. The foundation of invariant I1: canonical JSON is
``json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)``
and nothing else — this module delegates to :func:`auditors_trace.model.log.
canonical_json`, the single house authority. ``prev_record_sha256`` of the
first record is 64 zeros.

The hash excludes ``integrity`` and ``generated_at`` STRUCTURALLY (they never
enter the hashed payload), so a record's hash is identical whether it is
chained or not and whether it is display-stamped or not — which is exactly
what lets :func:`verify_chain` recompute and catch any tampered field.
"""

from __future__ import annotations

import hashlib

from auditors_trace.evidence.record import EvidenceRecord, Integrity
from auditors_trace.model.log import canonical_json as _house_canonical_json

GENESIS_HASH = "0" * 64


def canonical_json(obj: object) -> str:
    """Serialise deterministically: sorted keys, fixed separators, no ASCII escaping."""
    return _house_canonical_json(obj)


def sha256_hex(payload: str) -> str:
    """Return the hex sha256 of a UTF-8 encoded payload."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Return the hex sha256 of a byte payload (file-content pins)."""
    return hashlib.sha256(payload).hexdigest()


def violation_id(constraint_id: str, ocel_event_ids: tuple[str, ...], input_log_sha256: str) -> str:
    """The record's content-addressed id: sha256 first 16 hex (section 8).

    Canonical-JSON dict payload (house style, mirroring the injector's
    content addressing). Deliberately DISTINCT from the injector's
    ground-truth violation ids, which exclude the log hash (amendment C2) —
    the two id spaces are never compared.
    """
    payload = {
        "constraint_id": constraint_id,
        "input_log_sha256": input_log_sha256,
        "ocel_event_ids": sorted(ocel_event_ids),
    }
    return sha256_hex(canonical_json(payload))[:16]


def record_hash(record: EvidenceRecord) -> str:
    """Hash every field except ``integrity``, and never ``generated_at``."""
    payload = record.model_dump(mode="json", by_alias=True, exclude={"integrity", "generated_at"})
    return sha256_hex(canonical_json(payload))


def _sort_key(record: EvidenceRecord) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    # Identical to constraints.templates.violation_sort_key, materialised on
    # the record: chain order == the engine's canonical evaluate() order.
    return (
        record.constraint.id,
        record.evidence.ocel_event_ids,
        record.evidence.ocel_object_ids,
    )


def chain(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Sort records deterministically and link each to its predecessor's hash.

    Returns NEW records (the inputs stay unchained); idempotent when the
    input is already in canonical order. ``chain_index`` is the 0-based list
    position; one chain per rendered log.
    """
    linked: list[EvidenceRecord] = []
    previous = GENESIS_HASH
    for position, record in enumerate(sorted(records, key=_sort_key)):
        digest = record_hash(record)
        linked.append(
            record.model_copy(
                update={
                    "integrity": Integrity(
                        chain_index=position,
                        record_sha256=digest,
                        prev_record_sha256=previous,
                    )
                }
            )
        )
        previous = digest
    return linked


def records_jsonl(records: list[EvidenceRecord]) -> bytes:
    """The evidence bundle: one canonical-JSON line per record, in chain order.

    The single byte-level authority — the CLI, the goldens, and every
    byte-identity test serialise through here. ``generated_at`` is ALWAYS
    excluded (display-only; a stamped record must not change bundle bytes or
    violate the section 8 shape — adversarial review, 19 Aug 2026); a chained
    record's ``integrity`` is present by construction.
    """
    return "".join(
        canonical_json(
            record.model_dump(
                mode="json", by_alias=True, exclude_none=True, exclude={"generated_at"}
            )
        )
        + "\n"
        for record in records
    ).encode("utf-8")


def verify_chain(records: list[EvidenceRecord]) -> bool:
    """Return whether every link holds. Any mutated record breaks verification.

    Recomputes each record's hash (a forged ``record_sha256`` or any changed
    hashed field fails), and checks contiguous 0-based indices plus the
    genesis link. An empty chain is vacuously valid. Never raises.

    Scope, stated honestly: a bare hash chain is tamper-evident for CONTENT,
    not for COMPLETENESS — any strict prefix of a valid chain also verifies,
    as truncation resistance requires an anchor outside the bundle. Here that
    anchor is reproduction: re-running a record's ``rerun_command`` over the
    pinned inputs re-derives the FULL bundle byte-for-byte, so a censored
    bundle diverges from the reproduced one (adversarial review, 19 Aug 2026;
    pinned by test as a documented limitation, never an accident).
    """
    previous = GENESIS_HASH
    for position, record in enumerate(records):
        integrity = record.integrity
        if integrity is None:
            return False
        if integrity.chain_index != position:
            return False
        if integrity.record_sha256 != record_hash(record):
            return False
        if integrity.prev_record_sha256 != previous:
            return False
        previous = integrity.record_sha256
    return True

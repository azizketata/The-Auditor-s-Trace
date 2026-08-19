"""Canonical serialisation and hash chaining (Phase 6).

The chain is what makes the evidence log tamper-evident: verification
RECOMPUTES every record's hash, so any mutated field breaks the chain — not
just a forged link.
"""

from __future__ import annotations

from typing import Any

from evidence_payloads import record_dict

from auditors_trace.evidence.chain import (
    GENESIS_HASH,
    canonical_json,
    chain,
    record_hash,
    sha256_hex,
    verify_chain,
    violation_id,
)
from auditors_trace.evidence.record import EvidenceRecord, Integrity
from auditors_trace.model.log import canonical_json as model_canonical_json


def _record(**overrides: Any) -> EvidenceRecord:
    payload = record_dict()
    payload.update(overrides)
    return EvidenceRecord.model_validate(payload)


def _records() -> list[EvidenceRecord]:
    second = record_dict()
    second["constraint"]["id"] = "T4.reason_code_presence"
    second["severity"] = "medium"
    second["evidence"]["ocel_event_ids"] = ["EVT-SESS-0002-025-make_decision"]
    second["evidence"]["otel_span_ids"] = ["93c6c228d3b4a932"]
    third = record_dict()
    third["evidence"]["ocel_event_ids"] = ["EVT-SESS-0003-025-make_decision"]
    third["evidence"]["otel_span_ids"] = ["efc125d8a74f12e8"]
    return [
        EvidenceRecord.model_validate(second),
        EvidenceRecord.model_validate(third),
        _record(),
    ]


class TestCanonicalJson:
    def test_genesis_hash_is_64_zeros(self) -> None:
        assert GENESIS_HASH == "0" * 64

    def test_delegates_to_the_house_canonical_json(self) -> None:
        """One authority (model.log), byte-identical — including non-ASCII
        (the Kreditbüro tripwire: ensure_ascii=False, no \\u escapes)."""
        payload = {"controller": "Kreditbüro Süd GmbH", "z": 1, "a": [2, 3]}
        assert canonical_json(payload) == model_canonical_json(payload)
        assert "Kreditbüro" in canonical_json(payload)
        assert "\\u" not in canonical_json(payload)

    def test_sha256_hex_shape(self) -> None:
        digest = sha256_hex("payload")
        assert len(digest) == 64
        assert int(digest, 16) >= 0


class TestRecordHash:
    def test_stable_across_calls(self) -> None:
        assert record_hash(_record()) == record_hash(_record())

    def test_excludes_generated_at(self) -> None:
        base = _record()
        stamped = base.model_copy(update={"generated_at": "2026-08-19T12:00:00Z"})
        assert record_hash(stamped) == record_hash(base)

    def test_excludes_integrity(self) -> None:
        base = _record()
        linked = base.model_copy(
            update={
                "integrity": Integrity(
                    chain_index=0, record_sha256="1" * 64, prev_record_sha256=GENESIS_HASH
                )
            }
        )
        assert record_hash(linked) == record_hash(base)

    def test_any_hashed_field_changes_the_hash(self) -> None:
        assert record_hash(_record()) != record_hash(_record(violation_id="ffffffffffffffff"))


class TestViolationId:
    def test_content_addressed_and_order_insensitive(self) -> None:
        log_hash = "0" * 63 + "e"
        a = violation_id("T1.synchronised_approval", ("EV-2", "EV-1"), log_hash)
        b = violation_id("T1.synchronised_approval", ("EV-1", "EV-2"), log_hash)
        assert a == b
        assert len(a) == 16
        assert violation_id("T2.mandatory_data_coverage", ("EV-1", "EV-2"), log_hash) != a
        assert violation_id("T1.synchronised_approval", ("EV-1",), log_hash) != a
        assert violation_id("T1.synchronised_approval", ("EV-1", "EV-2"), "f" * 64) != a


class TestChain:
    def test_sorts_links_and_indexes(self) -> None:
        chained = chain(_records())
        keys = [
            (r.constraint.id, r.evidence.ocel_event_ids, r.evidence.ocel_object_ids)
            for r in chained
        ]
        assert keys == sorted(keys)  # engine-canonical order
        for position, record in enumerate(chained):
            assert record.integrity is not None
            assert record.integrity.chain_index == position
            assert record.integrity.record_sha256 == record_hash(record)
        assert chained[0].integrity is not None
        assert chained[0].integrity.prev_record_sha256 == GENESIS_HASH
        from itertools import pairwise

        for previous, current in pairwise(chained):
            assert previous.integrity is not None
            assert current.integrity is not None
            assert current.integrity.prev_record_sha256 == previous.integrity.record_sha256

    def test_input_is_not_mutated(self) -> None:
        records = _records()
        chain(records)
        assert all(record.integrity is None for record in records)

    def test_idempotent_on_canonical_input(self) -> None:
        once = chain(_records())
        twice = chain(once)
        assert twice == once


class TestVerifyChain:
    def test_valid_chain_verifies(self) -> None:
        assert verify_chain(chain(_records()))

    def test_empty_chain_is_vacuously_valid(self) -> None:
        assert verify_chain([])

    def test_mutated_record_detected(self) -> None:
        """The tamper case: change a hashed field, keep the integrity block."""
        chained = chain(_records())
        tampered_payload = record_dict()
        tampered_payload["severity"] = "low"  # the mutation
        tampered = EvidenceRecord.model_validate(tampered_payload).model_copy(
            update={"integrity": chained[-1].integrity}
        )
        assert not verify_chain([*chained[:-1], tampered])

    def test_swapped_order_detected(self) -> None:
        chained = chain(_records())
        assert not verify_chain([chained[1], chained[0], *chained[2:]])

    def test_forged_hash_detected(self) -> None:
        chained = chain(_records())
        forged = chained[0].model_copy(
            update={
                "integrity": Integrity(
                    chain_index=0, record_sha256="f" * 64, prev_record_sha256=GENESIS_HASH
                )
            }
        )
        assert not verify_chain([forged, *chained[1:]])

    def test_gap_in_chain_index_detected(self) -> None:
        chained = chain(_records())
        assert chained[1].integrity is not None
        shifted = chained[1].model_copy(
            update={"integrity": chained[1].integrity.model_copy(update={"chain_index": 5})}
        )
        assert not verify_chain([chained[0], shifted, *chained[2:]])

    def test_missing_integrity_detected(self) -> None:
        chained = chain(_records())
        assert not verify_chain([chained[0].model_copy(update={"integrity": None}), *chained[1:]])

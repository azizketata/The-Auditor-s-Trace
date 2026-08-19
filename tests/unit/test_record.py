"""The evidence record schema (Phase 6) — §8 exactly, nothing more.

The record is the artefact an auditor defends; its schema is therefore as
strict as the ruleset's: frozen models, closed key sets, substantive text
everywhere (no placeholder can ever reach audit evidence), and the exact §8
field inventory — no schema_version, session_id, or detail fields exist.
"""

from __future__ import annotations

from typing import Any

import pytest
from evidence_payloads import record_dict
from pydantic import ValidationError

from auditors_trace.constraints.ruleset import Severity
from auditors_trace.evidence.record import (
    RETENTION,
    Constraint,
    Context,
    EvidenceLocator,
    EvidenceRecord,
    Integrity,
    LegalBasis,
    Provenance,
    Reproducibility,
    Retention,
    StandardRef,
)


class TestConstruction:
    def test_full_section8_payload_validates(self) -> None:
        record = EvidenceRecord.model_validate(record_dict())
        assert record.severity is Severity.HIGH
        assert record.integrity is None  # unchained until chain() fills it
        assert record.generated_at is None

    def test_dump_round_trips_with_class_alias(self) -> None:
        record = EvidenceRecord.model_validate(record_dict())
        dumped = record.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["retention"]["class"] == "ai_act_art_26_6"
        assert "retention_class" not in dumped["retention"]
        assert EvidenceRecord.model_validate(dumped) == record

    def test_extra_keys_rejected_everywhere(self) -> None:
        payload = record_dict()
        payload["schema_version"] = 1  # §8 has no such field, deliberately
        with pytest.raises(ValidationError):
            EvidenceRecord.model_validate(payload)
        payload = record_dict()
        payload["evidence"]["session_id"] = "SESS-0001"
        with pytest.raises(ValidationError):
            EvidenceRecord.model_validate(payload)

    def test_models_are_frozen(self) -> None:
        record = EvidenceRecord.model_validate(record_dict())
        with pytest.raises(ValidationError):
            record.severity = Severity.LOW  # type: ignore[misc]

    def test_retention_constant_is_pinned_to_section8(self) -> None:
        assert RETENTION == Retention.model_validate(
            {
                "class": "ai_act_art_26_6",
                "min_retention_months": 6,
                "note": (
                    "financial institutions: retain per applicable Union financial services law"
                ),
            }
        )
        with pytest.raises(ValidationError):
            Retention.model_validate(
                {"class": "ai_act_art_26_6", "min_retention_months": 3, "note": "short"}
            )


class TestStandardRef:
    def test_each_of_the_three_shapes_validates(self) -> None:
        StandardRef.model_validate({"standard": "ISO/IEC 24970", "clause": "5.2"})
        StandardRef.model_validate({"standard": "ISO/IEC 42001", "control": "A.9.2"})
        StandardRef.model_validate({"framework": "NIST AI RMF", "function": "MEASURE"})

    def test_mixed_or_incomplete_shapes_rejected(self) -> None:
        bad_shapes: list[dict[str, str]] = [
            {},
            {"standard": "ISO/IEC 42001"},  # no clause/control
            {"clause": "5.2"},  # clause without standard
            {"framework": "NIST AI RMF"},  # no function
            {"function": "MEASURE"},  # function without framework
            {"standard": "ISO/IEC 42001", "clause": "5.2", "control": "A.9.2"},
            {"standard": "ISO/IEC 42001", "control": "A.9.2", "framework": "NIST AI RMF"},
            {
                "standard": "ISO/IEC 42001",
                "control": "A.9.2",
                "framework": "NIST AI RMF",
                "function": "MEASURE",
            },
        ]
        for payload in bad_shapes:
            with pytest.raises(ValidationError):
                StandardRef.model_validate(payload)

    def test_serializer_emits_only_the_populated_pair(self) -> None:
        ref = StandardRef.model_validate({"standard": "ISO/IEC 42001", "control": "A.9.2"})
        assert ref.model_dump(mode="json") == {"standard": "ISO/IEC 42001", "control": "A.9.2"}
        ref = StandardRef.model_validate({"framework": "NIST AI RMF", "function": "MEASURE"})
        assert ref.model_dump(mode="json") == {"framework": "NIST AI RMF", "function": "MEASURE"}

    def test_placeholders_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StandardRef.model_validate({"standard": "ISO/IEC 42001", "control": "TODO"})


class TestSubstance:
    def test_constraint_placeholders_rejected(self) -> None:
        for field in ("natural_language", "formal"):
            payload = record_dict()["constraint"]
            payload[field] = "TODO refine this"
            with pytest.raises(ValidationError):
                Constraint.model_validate(payload)

    def test_legal_basis_placeholders_rejected(self) -> None:
        payload = record_dict()["legal_basis"][0]
        payload["requirement"] = "TBD"
        with pytest.raises(ValidationError):
            LegalBasis.model_validate(payload)
        payload = record_dict()["legal_basis"][0]
        payload["article"] = "TBD"
        with pytest.raises(ValidationError):
            LegalBasis.model_validate(payload)

    def test_legal_basis_required_non_empty(self) -> None:
        payload = record_dict()
        payload["legal_basis"] = []
        with pytest.raises(ValidationError):
            EvidenceRecord.model_validate(payload)


class TestEvidenceLocator:
    def test_span_ids_pair_with_event_ids(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceLocator.model_validate(
                {
                    "ocel_event_ids": ["EV-A", "EV-B"],
                    "ocel_object_ids": [],
                    "otel_trace_id": "5f4d7e4c8991a7b690adaf603e2e12bf",
                    "otel_span_ids": ["3798dbdac2ca5d97"],  # one span for two events
                }
            )

    def test_duplicate_span_ids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceLocator.model_validate(
                {
                    "ocel_event_ids": ["EV-A", "EV-B"],
                    "ocel_object_ids": [],
                    "otel_trace_id": "5f4d7e4c8991a7b690adaf603e2e12bf",
                    "otel_span_ids": ["3798dbdac2ca5d97", "3798dbdac2ca5d97"],
                }
            )

    def test_object_ids_may_be_empty(self) -> None:
        # V4's ground truth legitimately cites no objects.
        locator = EvidenceLocator.model_validate(
            {
                "ocel_event_ids": ["EV-A"],
                "ocel_object_ids": [],
                "otel_trace_id": "5f4d7e4c8991a7b690adaf603e2e12bf",
                "otel_span_ids": ["3798dbdac2ca5d97"],
            }
        )
        assert locator.ocel_object_ids == ()

    def test_hex_id_shapes_enforced(self) -> None:
        for field, bad in (("otel_trace_id", "not-hex"), ("otel_span_ids", ["xyz"])):
            payload: dict[str, Any] = {
                "ocel_event_ids": ["EV-A"],
                "ocel_object_ids": [],
                "otel_trace_id": "5f4d7e4c8991a7b690adaf603e2e12bf",
                "otel_span_ids": ["3798dbdac2ca5d97"],
            }
            payload[field] = bad
            with pytest.raises(ValidationError):
                EvidenceLocator.model_validate(payload)


class TestDefaults:
    def test_context_defaults_are_empty(self) -> None:
        assert Context() == Context(policy_version="", model_versions={}, agent_versions={})

    def test_provenance_hash_shape_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Provenance.model_validate(
                {
                    "engine_version": "engine/0.1.0",
                    "engine_commit": "",
                    "input_log_sha256": "abc",  # not 64 hex
                }
            )

    def test_integrity_hash_shapes_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Integrity.model_validate(
                {"chain_index": -1, "record_sha256": "0" * 64, "prev_record_sha256": "0" * 64}
            )

    def test_reproducibility_carries_only_the_rerun_command(self) -> None:
        """B12: the record's own hash lives only in integrity; reproducibility
        has exactly one field."""
        assert set(Reproducibility.model_fields) == {"rerun_command"}

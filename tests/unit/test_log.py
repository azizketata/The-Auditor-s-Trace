"""Phase 1: the immutable OCEL model — canonicalisation, validation, hash.

Every rule R1-R15 exists because a serialisation layer or the hash chain
depends on it (docs/SPIKE-pm4py-roundtrip.md G1-G4); each gets a rejection
test so the guarantee is executable, not aspirational.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auditors_trace.model.log import (
    OCEL_E2O_QUALIFIERS,
    OCELEvent,
    OCELLog,
    OCELModelError,
    OCELObject,
    OCELRelation,
    canonical_json,
    events_sorted,
    log_hash,
    objects_sorted,
)
from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import E2O_QUALIFIERS, O2ORef


def _objects() -> tuple[OCELObject, ...]:
    return (
        OCELObject(
            object_id="SES-1",
            object_type=ObjectType.SESSION,
            attributes=(("session_id", "SES-1"), ("workflow_name", "credit_scoring")),
        ),
        OCELObject(
            object_id="APP-1",
            object_type=ObjectType.APPLICATION,
            attributes=(("amount", 1000), ("application_id", "APP-1")),
        ),
        OCELObject(
            object_id="CUST-1",
            object_type=ObjectType.APPLICANT,
            attributes=(("applicant_id", "CUST-1"), ("jurisdiction", "DE")),
        ),
    )


def _event(**overrides: Any) -> OCELEvent:
    spec: dict[str, Any] = {
        "event_id": "EV-1",
        "event_type": EventType.SESSION_START,
        "timestamp": "2026-03-02T09:00:00Z",
        "attributes": (("scenario_name", "credit_scoring"), ("seed", 42)),
        "relations": (
            OCELRelation(object_id="SES-1", qualifier=Qualifier.DECLARES),
            OCELRelation(object_id="APP-1", qualifier=Qualifier.DECLARES),
            OCELRelation(object_id="CUST-1", qualifier=Qualifier.DECLARES),
        ),
    }
    spec.update(overrides)
    return OCELEvent(**spec)


def _log(**overrides: Any) -> OCELLog:
    spec: dict[str, Any] = {
        "events": (_event(),),
        "objects": _objects(),
        "o2o": (O2ORef(source_id="APP-1", target_id="CUST-1", qualifier=Qualifier.SUBMITTED_BY),),
    }
    spec.update(overrides)
    return OCELLog(**spec)


class TestCanonicalisation:
    def test_construction_order_is_irrelevant(self) -> None:
        base = _log()
        shuffled = OCELLog(
            events=tuple(reversed(base.events)),
            objects=tuple(reversed(base.objects)),
            o2o=tuple(reversed(base.o2o)),
        )
        assert shuffled == base

    def test_attribute_and_relation_order_is_irrelevant(self) -> None:
        a = _event()
        b = _event(
            attributes=(("seed", 42), ("scenario_name", "credit_scoring")),
            relations=tuple(reversed(_event().relations)),
        )
        assert a == b

    def test_sorted_accessors(self) -> None:
        second = _event(
            event_id="EV-2",
            event_type=EventType.SESSION_END,
            timestamp="2026-03-02T09:00:10Z",
            attributes=(("duration_seconds", 10), ("event_count", 2), ("outcome", "grant")),
            relations=(OCELRelation(object_id="SES-1", qualifier=Qualifier.WITHIN),),
        )
        log = _log(events=(second, _event()))
        assert [e.event_id for e in events_sorted(log)] == ["EV-1", "EV-2"]
        assert [o.object_id for o in objects_sorted(log)] == ["CUST-1", "APP-1", "SES-1"]

    def test_ocel_e2o_qualifiers_is_the_span_set_plus_declares(self) -> None:
        assert OCEL_E2O_QUALIFIERS == E2O_QUALIFIERS | {Qualifier.DECLARES}

    def test_canonical_json_matches_house_rule(self) -> None:
        assert canonical_json({"b": 1, "a": ["x", 2]}) == '{"a":["x",2],"b":1}'


class TestValidationRejects:
    def test_r1_empty_id(self) -> None:
        with pytest.raises(OCELModelError, match="empty"):
            OCELObject(object_id="", object_type=ObjectType.SESSION, attributes=())

    def test_r2_numeric_literal_id(self) -> None:
        with pytest.raises(OCELModelError, match="numeric"):
            OCELObject(
                object_id="42.0",
                object_type=ObjectType.SESSION,
                attributes=(("session_id", "s"), ("workflow_name", "w")),
            )

    @pytest.mark.parametrize("bad_id", ["SES-\\x0", "SES A", "SES\tA", "SÉS-1", "-SES"])
    def test_r2_ids_outside_the_safe_charset(self, bad_id: str) -> None:
        # pm4py's sqlite id normaliser truncates ids ending in backslash+char+0
        # (review finding, 19 Aug 2026); the charset ban covers the whole class.
        with pytest.raises(OCELModelError, match="charset"):
            OCELObject(
                object_id=bad_id,
                object_type=ObjectType.SESSION,
                attributes=(("session_id", "s"), ("workflow_name", "w")),
            )

    def test_r3_duplicate_attribute_name(self) -> None:
        with pytest.raises(OCELModelError, match="duplicate"):
            _event(attributes=(("seed", 1), ("seed", 2)))

    def test_r4_unknown_attribute_name(self) -> None:
        with pytest.raises(OCELModelError, match="unknown attribute"):
            _event(attributes=(("frobnication_level", 9),))

    def test_r4_kind_mismatch_bool_where_int(self) -> None:
        with pytest.raises(OCELModelError, match="integer"):
            _event(attributes=(("seed", True),))

    def test_r4_kind_mismatch_str_where_list(self) -> None:
        with pytest.raises(OCELModelError, match="string_list"):
            OCELObject(
                object_id="DEC-1",
                object_type=ObjectType.CREDIT_DECISION,
                attributes=(("reason_codes", "RC01"),),
            )

    def test_r4_non_finite_float(self) -> None:
        with pytest.raises(OCELModelError, match="finite"):
            OCELEvent(
                event_id="EV-L",
                event_type=EventType.CALL_LLM,
                timestamp="2026-03-02T09:00:01Z",
                attributes=(("temperature", float("nan")),),
                relations=(OCELRelation(object_id="MDL-1", qualifier=Qualifier.USES),),
            )

    @pytest.mark.parametrize("poison", ["null", "None", "NULL"])
    def test_r5_unrepresentable_string_values(self, poison: str) -> None:
        with pytest.raises(OCELModelError, match="unrepresentable"):
            OCELObject(
                object_id="RES-1",
                object_type=ObjectType.DATA_RESOURCE,
                attributes=(("lawful_basis", poison),),
            )

    @pytest.mark.parametrize("poison", ["a\rb", "a\x01b", "a\x00b", "line1\r\nline2"])
    def test_r5_control_characters_rejected(self, poison: str) -> None:
        # XML-1.0 line-ending normalisation silently rewrites \r and XML
        # forbids most control chars (review finding, 19 Aug 2026).
        with pytest.raises(OCELModelError, match="control"):
            OCELObject(
                object_id="RES-1",
                object_type=ObjectType.DATA_RESOURCE,
                attributes=(("lawful_basis", poison),),
            )

    def test_r5_newline_and_tab_are_admitted(self) -> None:
        obj = OCELObject(
            object_id="RES-1",
            object_type=ObjectType.DATA_RESOURCE,
            attributes=(("lawful_basis", "line1\nline2\tend"),),
        )
        assert dict(obj.attributes)["lawful_basis"] == "line1\nline2\tend"

    def test_r5_lone_surrogate_rejected(self) -> None:
        # A lone surrogate constructs a log that log_hash could never encode
        # (review finding, 19 Aug 2026) - rejected at the gate instead.
        with pytest.raises(OCELModelError, match="surrogate"):
            OCELObject(
                object_id="RES-1",
                object_type=ObjectType.DATA_RESOURCE,
                attributes=(("lawful_basis", "\ud800"),),
            )

    def test_r5_string_list_items_get_the_same_hygiene(self) -> None:
        with pytest.raises(OCELModelError, match="control"):
            OCELObject(
                object_id="DEC-1",
                object_type=ObjectType.CREDIT_DECISION,
                attributes=(("reason_codes", ("RC01", "RC\r02")),),
            )

    def test_r4_integer_beyond_ieee_exact_bound(self) -> None:
        # pm4py pools attribute columns to float64; 2**53+1 would silently
        # truncate on the JSON path (review finding, 19 Aug 2026).
        with pytest.raises(OCELModelError, match="IEEE-754"):
            OCELObject(
                object_id="APP-1",
                object_type=ObjectType.APPLICATION,
                attributes=(("amount", 2**53 + 1), ("application_id", "APP-1")),
            )

    def test_negative_zero_is_canonicalised_not_rejected(self) -> None:
        # 0.0 and -0.0 are equal but canonical-JSON-spell differently; one
        # spelling enters the model (review finding, 19 Aug 2026).
        def call_llm(temperature: float) -> OCELEvent:
            return OCELEvent(
                event_id="EV-L",
                event_type=EventType.CALL_LLM,
                timestamp="2026-03-02T09:00:01Z",
                attributes=(("temperature", temperature),),
                relations=(OCELRelation(object_id="MDL-1", qualifier=Qualifier.USES),),
            )

        assert call_llm(-0.0) == call_llm(0.0)
        assert repr(dict(call_llm(-0.0).attributes)["temperature"]) == "0.0"

    @pytest.mark.parametrize(
        "timestamp",
        [
            "2026-03-02T09:00:00",  # no Z
            "2026-03-02T09:00:00+00:00",  # offset spelling, not canonical
            "2026-03-02T09:00:00.500Z",  # sub-second
            "2026-03-02 09:00:00Z",  # space separator
            "not-a-time",
        ],
    )
    def test_r6_non_canonical_timestamps(self, timestamp: str) -> None:
        with pytest.raises(OCELModelError, match="timestamp"):
            _event(timestamp=timestamp)

    def test_r7_duplicate_event_object_pair(self) -> None:
        with pytest.raises(OCELModelError, match="one relation"):
            _event(
                relations=(
                    OCELRelation(object_id="SES-1", qualifier=Qualifier.DECLARES),
                    OCELRelation(object_id="SES-1", qualifier=Qualifier.WITHIN),
                )
            )

    def test_r8_o2o_qualifier_in_e2o_position(self) -> None:
        with pytest.raises(OCELModelError, match="o2o qualifier"):
            _event(relations=(OCELRelation(object_id="SES-1", qualifier=Qualifier.SUBMITTED_BY),))

    def test_r9_event_without_relations(self) -> None:
        with pytest.raises(OCELModelError, match="at least one relation"):
            _event(relations=())

    def test_r10_duplicate_event_ids(self) -> None:
        with pytest.raises(OCELModelError, match="event id"):
            _log(events=(_event(), _event()))

    def test_r11_duplicate_object_ids(self) -> None:
        duplicate = (
            *_objects(),
            OCELObject(
                object_id="SES-1", object_type=ObjectType.SESSION, attributes=(("session_id", "x"),)
            ),
        )
        with pytest.raises(OCELModelError, match="object id"):
            _log(objects=duplicate)

    def test_r12_relation_to_undeclared_object(self) -> None:
        with pytest.raises(OCELModelError, match="undeclared"):
            _log(
                events=(
                    _event(
                        relations=(
                            *_event().relations,
                            OCELRelation(object_id="GHOST-1", qualifier=Qualifier.CONCERNS),
                        )
                    ),
                )
            )

    def test_r13_object_without_any_relation(self) -> None:
        orphan = (
            *_objects(),
            OCELObject(
                object_id="POL-ORPHAN",
                object_type=ObjectType.POLICY_VERSION,
                attributes=(("policy_id", "P"), ("version", "1")),
            ),
        )
        with pytest.raises(OCELModelError, match="no E2O relation"):
            _log(objects=orphan)

    def test_r14_qualifier_illegal_for_pair(self) -> None:
        # session_start may not `read` a Session.
        with pytest.raises(OCELModelError, match="not permitted"):
            _log(
                events=(
                    _event(
                        relations=(
                            OCELRelation(object_id="SES-1", qualifier=Qualifier.READS),
                            OCELRelation(object_id="APP-1", qualifier=Qualifier.DECLARES),
                            OCELRelation(object_id="CUST-1", qualifier=Qualifier.DECLARES),
                        )
                    ),
                )
            )

    def test_r15_o2o_endpoint_type_mismatch(self) -> None:
        wrong = (O2ORef(source_id="CUST-1", target_id="APP-1", qualifier=Qualifier.SUBMITTED_BY),)
        with pytest.raises(OCELModelError, match="o2o"):
            _log(o2o=wrong)

    def test_r15_o2o_unresolved_endpoint(self) -> None:
        ghost = (O2ORef(source_id="APP-1", target_id="GHOST-1", qualifier=Qualifier.SUBMITTED_BY),)
        with pytest.raises(OCELModelError, match="o2o"):
            _log(o2o=ghost)

    def test_r15_duplicate_o2o_triple(self) -> None:
        pair = O2ORef(source_id="APP-1", target_id="CUST-1", qualifier=Qualifier.SUBMITTED_BY)
        with pytest.raises(OCELModelError, match="duplicate"):
            _log(o2o=(pair, pair))


class TestLogHash:
    @given(st.randoms())
    def test_permutation_invariance(self, rng: random.Random) -> None:
        base = _log()
        events = list(base.events)
        objects = list(base.objects)
        o2o = list(base.o2o)
        rng.shuffle(events)
        rng.shuffle(objects)
        rng.shuffle(o2o)
        shuffled = OCELLog(events=tuple(events), objects=tuple(objects), o2o=tuple(o2o))
        assert log_hash(shuffled) == log_hash(base)

    def test_hash_is_sha256_hex(self) -> None:
        digest = log_hash(_log())
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")

    def test_every_content_class_is_hashed(self) -> None:
        base = _log()
        variants = [
            _log(o2o=()),  # o2o removed
            _log(events=(_event(timestamp="2026-03-02T09:00:01Z"),)),  # time
            _log(
                events=(
                    _event(
                        attributes=(("scenario_name", "x"), ("seed", 42)),
                    ),
                )
            ),  # ev attr
        ]
        hashes = {log_hash(v) for v in variants}
        assert log_hash(base) not in hashes
        assert len(hashes) == len(variants)

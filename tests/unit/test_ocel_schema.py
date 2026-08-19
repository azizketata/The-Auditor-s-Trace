"""Phase 1: the attribute-kind registry and the qualifier matrix.

These two tables are the semantic authority the OCEL model validates against.
The matrix is bound to reality twice: §5's example rows are asserted
verbatim, and every qualified (event, object) pair occurring in the frozen
golden scenario spans must be allowed — so the matrix can never drift from
what the Phase 2 scenario actually emits (and what Phase 3 will map).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditors_trace.model.ocel_schema import (
    AttributeKind,
    EventType,
    ObjectType,
    Qualifier,
    allowed_qualifiers,
    declaring_events,
    event_attribute_kinds,
    o2o_endpoints,
    object_attribute_kinds,
    object_type_attributes,
)
from auditors_trace.model.span_contract import (
    REQUIRED_EVENT_ATTRS,
    parse_event,
)

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "spans"

#: BUILD-PLAN §5's "key attributes", verbatim — the required subset of the
#: scenario's frozen superset.
SECTION_5_ATTRIBUTES = {
    ObjectType.APPLICATION: {"application_id", "product_type", "amount", "submitted_at"},
    ObjectType.APPLICANT: {"applicant_id", "jurisdiction"},
    ObjectType.AGENT: {"agent_id", "name", "role", "version", "framework"},
    ObjectType.CREDIT_DECISION: {"decision_id", "outcome", "score", "reason_codes"},
    ObjectType.APPROVAL: {"approval_id", "approver_role", "granted"},
    ObjectType.HUMAN_APPROVER: {"approver_id", "role", "authority_level"},
    ObjectType.PROMPT: {"prompt_name", "prompt_version", "template_hash"},
    ObjectType.MODEL: {"model_id", "provider", "version"},
    ObjectType.TOOL: {"tool_name", "tool_type"},
    ObjectType.DATA_RESOURCE: {"resource_id", "classification", "lawful_basis"},
    ObjectType.POLICY_VERSION: {"policy_id", "version", "effective_from", "effective_to"},
    ObjectType.SESSION: {"session_id", "workflow_name"},
}


class TestObjectAttributeRegistry:
    @pytest.mark.parametrize("object_type", list(ObjectType))
    def test_names_are_sorted_and_match_kinds_table(self, object_type: ObjectType) -> None:
        names = object_type_attributes(object_type)
        assert names == tuple(sorted(names))
        assert set(names) == set(object_attribute_kinds(object_type))
        assert names  # no object type is attribute-free

    @pytest.mark.parametrize("object_type", list(ObjectType))
    def test_section_5_key_attributes_are_present(self, object_type: ObjectType) -> None:
        assert SECTION_5_ATTRIBUTES[object_type] <= set(object_type_attributes(object_type))

    def test_kind_spot_checks(self) -> None:
        assert object_attribute_kinds(ObjectType.APPROVAL)["granted"] is AttributeKind.BOOLEAN
        decision = object_attribute_kinds(ObjectType.CREDIT_DECISION)
        assert decision["score"] is AttributeKind.INTEGER
        assert decision["reason_codes"] is AttributeKind.STRING_LIST
        application = object_attribute_kinds(ObjectType.APPLICATION)
        assert application["amount"] is AttributeKind.INTEGER
        assert application["term_months"] is AttributeKind.INTEGER


class TestEventAttributeRegistry:
    @pytest.mark.parametrize("event_type", list(EventType))
    def test_covers_the_span_contracts_required_attrs(self, event_type: EventType) -> None:
        # ocel_schema cannot import REQUIRED_EVENT_ATTRS (span_contract imports
        # ocel_schema) — this test is the drift guard across that boundary.
        assert set(REQUIRED_EVENT_ATTRS[event_type]) <= set(event_attribute_kinds(event_type))

    def test_kind_spot_checks(self) -> None:
        assert event_attribute_kinds(EventType.MAKE_DECISION)["adverse"] is AttributeKind.BOOLEAN
        assert (
            event_attribute_kinds(EventType.MAKE_DECISION)["reason_codes"]
            is AttributeKind.STRING_LIST
        )
        assert event_attribute_kinds(EventType.CALL_LLM)["temperature"] is AttributeKind.FLOAT
        assert event_attribute_kinds(EventType.CALL_LLM)["model_seed"] is AttributeKind.INTEGER
        assert event_attribute_kinds(EventType.SESSION_END)["duration_seconds"] is (
            AttributeKind.INTEGER
        )


class TestAllowedQualifiers:
    def test_section_5_example_rows_verbatim(self) -> None:
        # make_decision row
        assert Qualifier.PRODUCES in allowed_qualifiers(
            EventType.MAKE_DECISION, ObjectType.CREDIT_DECISION
        )
        assert Qualifier.CONCERNS in allowed_qualifiers(
            EventType.MAKE_DECISION, ObjectType.APPLICATION
        )
        assert Qualifier.CONCERNS in allowed_qualifiers(
            EventType.MAKE_DECISION, ObjectType.APPLICANT
        )
        assert Qualifier.GOVERNED_BY in allowed_qualifiers(
            EventType.MAKE_DECISION, ObjectType.POLICY_VERSION
        )
        assert Qualifier.PERFORMED_BY in allowed_qualifiers(
            EventType.MAKE_DECISION, ObjectType.AGENT
        )
        # retrieve_data row
        assert Qualifier.READS in allowed_qualifiers(
            EventType.RETRIEVE_DATA, ObjectType.DATA_RESOURCE
        )
        assert Qualifier.CONCERNS in allowed_qualifiers(
            EventType.RETRIEVE_DATA, ObjectType.APPLICATION
        )
        assert Qualifier.PERFORMED_BY in allowed_qualifiers(
            EventType.RETRIEVE_DATA, ObjectType.AGENT
        )
        # grant_approval row
        assert Qualifier.PRODUCES in allowed_qualifiers(
            EventType.GRANT_APPROVAL, ObjectType.APPROVAL
        )
        assert Qualifier.PERFORMED_BY in allowed_qualifiers(
            EventType.GRANT_APPROVAL, ObjectType.HUMAN_APPROVER
        )
        assert Qualifier.APPROVES in allowed_qualifiers(
            EventType.GRANT_APPROVAL, ObjectType.CREDIT_DECISION
        )
        # handoff row
        handoff_agent = allowed_qualifiers(EventType.HANDOFF, ObjectType.AGENT)
        assert Qualifier.FROM in handoff_agent
        assert Qualifier.TO in handoff_agent
        assert Qualifier.WITHIN in allowed_qualifiers(EventType.HANDOFF, ObjectType.SESSION)

    def test_a_denial_concerns_and_never_approves(self) -> None:
        denial = allowed_qualifiers(EventType.DENY_APPROVAL, ObjectType.CREDIT_DECISION)
        assert Qualifier.CONCERNS in denial
        assert Qualifier.APPROVES not in denial

    def test_declares_exactly_at_declaring_pairs(self) -> None:
        for object_type in ObjectType:
            declarers = declaring_events(object_type)
            assert declarers  # every object type has a declaring event
            for event_type in EventType:
                allowed = allowed_qualifiers(event_type, object_type)
                if event_type in declarers:
                    assert Qualifier.DECLARES in allowed
                else:
                    assert Qualifier.DECLARES not in allowed

    def test_unlisted_pairs_are_empty(self) -> None:
        assert allowed_qualifiers(EventType.CALL_TOOL, ObjectType.HUMAN_APPROVER) == ()
        assert allowed_qualifiers(EventType.SESSION_END, ObjectType.MODEL) == ()

    def test_returned_tuples_are_sorted_and_deduplicated(self) -> None:
        for event_type in EventType:
            for object_type in ObjectType:
                allowed = allowed_qualifiers(event_type, object_type)
                values = [q.value for q in allowed]
                assert values == sorted(set(values))

    def test_matrix_covers_the_frozen_golden_scenario_output(self) -> None:
        """Every qualified triple in the committed golden spans is legal.

        This binds the matrix to the Phase 2 scenario's frozen reality: if
        the matrix forgot a pair the scenario emits, Phase 3's mapper output
        could never validate, and we want that contradiction surfaced here.
        """
        triples: set[tuple[EventType, ObjectType, Qualifier]] = set()
        for spans in GOLDEN.glob("credit_*.jsonl"):
            for line in spans.read_text(encoding="utf-8").splitlines():
                attributes = json.loads(line).get("attributes", {})
                spec = parse_event(attributes)
                if spec is None:
                    continue
                for ref in spec.objects:
                    if ref.qualifier is not None:
                        triples.add((spec.event_type, ref.object_type, ref.qualifier))
        assert triples  # the goldens really contain qualified relations
        for event_type, object_type, qualifier in sorted(
            triples, key=lambda t: (t[0].value, t[1].value, t[2].value)
        ):
            assert qualifier in allowed_qualifiers(event_type, object_type), (
                f"golden scenario emits {event_type.value} -> {object_type.value} "
                f"[{qualifier.value}] but the matrix does not allow it"
            )


class TestO2OEndpoints:
    def test_the_three_rules(self) -> None:
        assert o2o_endpoints(Qualifier.DELEGATES_TO) == (ObjectType.AGENT, ObjectType.AGENT)
        assert o2o_endpoints(Qualifier.DERIVED_FROM) == (
            ObjectType.CREDIT_DECISION,
            ObjectType.APPLICATION,
        )
        assert o2o_endpoints(Qualifier.SUBMITTED_BY) == (
            ObjectType.APPLICATION,
            ObjectType.APPLICANT,
        )

    def test_non_o2o_qualifier_raises(self) -> None:
        with pytest.raises(ValueError, match="not an o2o qualifier"):
            o2o_endpoints(Qualifier.PRODUCES)

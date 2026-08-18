"""The span contract round-trips exactly and rejects malformed encodings.

``test_span_contract_roundtrip`` is the highest-value test in Phase 2: it makes
Phase 3's reader correct by construction and stops writer/reader drift.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import (
    E2O_QUALIFIERS,
    EVENT_TYPE,
    O2O_QUALIFIERS,
    OBJ_COUNT,
    EventSpec,
    O2ORef,
    ObjectRef,
    SessionScope,
    SpanContractError,
    flatten_event,
    flatten_scope,
    governance_census,
    obj_attr_key,
    obj_key,
    parse_event,
    parse_scope,
    validate_event,
)

_ids = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-", min_size=1, max_size=24).filter(
    lambda s: s.strip("-") != ""
)
_attr_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=16)
_attr_values = st.one_of(
    st.text(max_size=40),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.lists(st.text(max_size=10), max_size=4).map(tuple),
)
_attr_pairs = st.dictionaries(_attr_names, _attr_values, max_size=5).map(lambda d: tuple(d.items()))

_object_refs = st.builds(
    ObjectRef,
    object_type=st.sampled_from(list(ObjectType)),
    object_id=_ids,
    qualifier=st.one_of(st.none(), st.sampled_from(sorted(E2O_QUALIFIERS))),
    declares=st.booleans(),
    attributes=_attr_pairs,
).map(
    # keep entries self-consistent: declarations carry attrs, references don't
    lambda r: ObjectRef(
        r.object_type,
        r.object_id,
        r.qualifier,
        declares=bool(r.attributes),
        attributes=r.attributes,
    )
)

_o2o_refs = st.builds(
    O2ORef,
    source_id=_ids,
    target_id=_ids,
    qualifier=st.sampled_from(sorted(O2O_QUALIFIERS)),
)


def _dedupe_objects(refs: tuple[ObjectRef, ...]) -> tuple[ObjectRef, ...]:
    seen: set[str] = set()
    out = []
    for r in refs:
        if r.object_id not in seen:
            seen.add(r.object_id)
            out.append(r)
    return tuple(out)


_event_specs = st.builds(
    EventSpec,
    event_type=st.sampled_from(list(EventType)),
    event_id=_ids,
    seq=st.integers(min_value=1, max_value=10_000),
    time=st.just("2026-03-02T09:00:01Z"),
    actor_id=st.one_of(st.none(), _ids),
    actor_type=st.none(),
    attributes=_attr_pairs,
    objects=st.lists(_object_refs, max_size=8).map(tuple).map(_dedupe_objects),
    o2o=st.lists(_o2o_refs, max_size=3).map(tuple),
).map(
    lambda e: EventSpec(
        event_type=e.event_type,
        event_id=e.event_id,
        seq=e.seq,
        time=e.time,
        actor_id=e.actor_id,
        actor_type=ObjectType.AGENT if e.actor_id is not None else None,
        attributes=e.attributes,
        objects=e.objects,
        o2o=e.o2o,
    )
)


class TestRoundtrip:
    @settings(max_examples=200, deadline=None)
    @given(spec=_event_specs)
    def test_span_contract_roundtrip(self, spec: EventSpec) -> None:
        assert parse_event(flatten_event(spec)) == spec

    @settings(max_examples=50, deadline=None)
    @given(spec=_event_specs)
    def test_flatten_is_order_independent(self, spec: EventSpec) -> None:
        reversed_spec = EventSpec(
            event_type=spec.event_type,
            event_id=spec.event_id,
            seq=spec.seq,
            time=spec.time,
            actor_id=spec.actor_id,
            actor_type=spec.actor_type,
            attributes=tuple(reversed(spec.attributes)),
            objects=tuple(reversed(spec.objects)),
            o2o=tuple(reversed(spec.o2o)),
        )
        assert flatten_event(reversed_spec) == flatten_event(spec)

    def test_scope_roundtrip(self) -> None:
        scope = SessionScope(
            scenario_name="credit_scoring",
            scenario_version="1.0.0",
            run_id="abc123",
            run_seed=42,
            session_id="SESS-0000",
            workflow_name="credit_application",
            application_id="APP-1",
            applicant_id="APN-1",
        )
        assert parse_scope(flatten_scope(scope)) == scope

    def test_parse_scope_absent_returns_none(self) -> None:
        assert parse_scope({"foo": "bar"}) is None

    def test_parse_scope_unknown_contract_raises(self) -> None:
        with pytest.raises(SpanContractError, match="unknown span contract"):
            parse_scope({"at.contract": "at-span/999"})


def _minimal_attrs() -> dict[str, object]:
    """A syntactically complete one-object event encoding to mutate."""
    spec = EventSpec(
        event_type=EventType.HANDOFF,
        event_id="EVT-1",
        seq=1,
        time="2026-03-02T09:00:01Z",
        objects=(
            ObjectRef(
                ObjectType.AGENT,
                "AGT-1",
                Qualifier.FROM,
                declares=True,
                attributes=(("name", "a"),),
            ),
        ),
    )
    return dict(flatten_event(spec))


class TestMalformedRaisesNotDrops:
    def test_absent_event_type_returns_none(self) -> None:
        attrs = _minimal_attrs()
        del attrs[EVENT_TYPE]
        assert parse_event(attrs) is None

    def test_unknown_event_type_raises(self) -> None:
        attrs = _minimal_attrs()
        attrs[EVENT_TYPE] = "not_an_event"
        with pytest.raises(SpanContractError):
            parse_event(attrs)

    def test_count_higher_than_entries_raises(self) -> None:
        attrs = _minimal_attrs()
        attrs[OBJ_COUNT] = 3
        with pytest.raises(SpanContractError, match="missing"):
            parse_event(attrs)

    def test_index_out_of_range_raises(self) -> None:
        attrs = _minimal_attrs()
        attrs[obj_key(7, "type")] = ObjectType.AGENT.value
        attrs[obj_key(7, "id")] = "AGT-7"
        with pytest.raises(SpanContractError):
            parse_event(attrs)

    def test_missing_object_id_raises(self) -> None:
        attrs = _minimal_attrs()
        del attrs[obj_key(0, "id")]
        with pytest.raises(SpanContractError, match="missing or empty"):
            parse_event(attrs)

    def test_unknown_qualifier_raises(self) -> None:
        attrs = _minimal_attrs()
        attrs[obj_key(0, "qualifier")] = "sponsors"
        with pytest.raises(SpanContractError, match="unknown value"):
            parse_event(attrs)

    def test_o2o_qualifier_on_object_raises(self) -> None:
        attrs = _minimal_attrs()
        attrs[obj_key(0, "qualifier")] = Qualifier.DELEGATES_TO.value
        with pytest.raises(SpanContractError, match="O2O qualifier"):
            parse_event(attrs)

    def test_declaration_without_attributes_raises(self) -> None:
        attrs = _minimal_attrs()
        del attrs[obj_attr_key(0, "name")]
        with pytest.raises(SpanContractError, match="no attributes"):
            parse_event(attrs)

    def test_attributes_without_declaration_raises(self) -> None:
        attrs = _minimal_attrs()
        del attrs[obj_key(0, "declares")]
        with pytest.raises(SpanContractError, match="without declares"):
            parse_event(attrs)


class TestConstructionRejectsSilentDrops:
    """Everything OTel or a dict encoding would drop silently is rejected
    loudly at construction or validation time instead."""

    def test_duplicate_attribute_names_raise(self) -> None:
        with pytest.raises(SpanContractError, match="duplicate attribute name"):
            ObjectRef(
                ObjectType.AGENT,
                "A",
                Qualifier.FROM,
                declares=True,
                attributes=(("version", "1"), ("version", "2")),
            )

    def test_empty_attribute_name_raises(self) -> None:
        with pytest.raises(SpanContractError, match="non-empty"):
            EventSpec(
                event_type=EventType.HANDOFF,
                event_id="EVT-1",
                seq=1,
                time="2026-03-02T09:00:01Z",
                attributes=(("", "value"),),
            )

    @pytest.mark.parametrize("value", [None, {"a": 1}, {1, 2}, b"bytes"])
    def test_unrepresentable_value_types_raise(self, value: object) -> None:
        with pytest.raises(SpanContractError, match="silently dropped"):
            EventSpec(
                event_type=EventType.HANDOFF,
                event_id="EVT-1",
                seq=1,
                time="2026-03-02T09:00:01Z",
                attributes=(("model_seed", value),),  # type: ignore[arg-type]
            )

    def test_attributes_without_declaration_fail_validation(self) -> None:
        # flatten_event would silently drop them; validate refuses first.
        spec = EventSpec(
            event_type=EventType.HANDOFF,
            event_id="EVT-1",
            seq=1,
            time="2026-03-02T09:00:01Z",
            actor_id="AGT-1",
            actor_type=ObjectType.AGENT,
            attributes=(("handoff_kind", "sequential"), ("reason", "done")),
            objects=(
                ObjectRef(
                    ObjectType.TOOL, "TOOL-x", Qualifier.USES, attributes=(("tool_type", "api"),)
                ),
            ),
        )
        with pytest.raises(SpanContractError, match="without declares"):
            validate_event(spec)


def _valid_decision_spec(
    *,
    actor: tuple[ObjectType, str] | None = (ObjectType.AGENT, "AGT-1"),
    adverse: bool = True,
    reason_codes: tuple[str, ...] = ("RC01",),
    event_type: EventType = EventType.MAKE_DECISION,
    attrs: tuple[tuple[str, object], ...] | None = None,
) -> EventSpec:
    default_attrs: tuple[tuple[str, object], ...] = (
        ("outcome", "deny"),
        ("score", 400),
        ("reason_codes", reason_codes),
        ("adverse", adverse),
    )
    return EventSpec(
        event_type=event_type,
        event_id="EVT-1",
        seq=1,
        time="2026-03-02T09:00:01Z",
        actor_id=actor[1] if actor else None,
        actor_type=actor[0] if actor else None,
        attributes=attrs if attrs is not None else default_attrs,  # type: ignore[arg-type]
    )


class TestActorPolicyAndSemanticRules:
    def test_session_bounds_with_an_actor_fail_validation(self) -> None:
        spec = EventSpec(
            event_type=EventType.SESSION_START,
            event_id="EVT-1",
            seq=1,
            time="2026-03-02T09:00:01Z",
            actor_id="AGT-1",
            actor_type=ObjectType.AGENT,
            attributes=(
                ("contract", "at-span/1"),
                ("run_id", "x"),
                ("scenario_name", "s"),
                ("scenario_version", "1"),
                ("seed", 1),
            ),
        )
        with pytest.raises(SpanContractError, match="must carry no actor"):
            validate_event(spec)

    def test_decision_without_an_actor_fails_validation(self) -> None:
        with pytest.raises(SpanContractError, match="requires an actor"):
            validate_event(_valid_decision_spec(actor=None))

    def test_agent_cannot_grant_approval(self) -> None:
        spec = EventSpec(
            event_type=EventType.GRANT_APPROVAL,
            event_id="EVT-1",
            seq=1,
            time="2026-03-02T09:00:01Z",
            actor_id="AGT-1",
            actor_type=ObjectType.AGENT,
            attributes=(
                ("approver_role", "credit_officer"),
                ("authority_level", 1),
                ("rationale_hash", "abc"),
            ),
        )
        with pytest.raises(SpanContractError, match="actor must be"):
            validate_event(spec)

    def test_adverse_decision_without_reason_codes_fails_validation(self) -> None:
        with pytest.raises(SpanContractError, match="non-empty reason_codes"):
            validate_event(_valid_decision_spec(reason_codes=()))

    def test_adverse_decision_with_reason_codes_validates(self) -> None:
        validate_event(_valid_decision_spec())

    def test_adverse_notice_must_contain_reason_codes(self) -> None:
        spec = _valid_decision_spec(
            event_type=EventType.NOTIFY_APPLICANT,
            attrs=(
                ("channel", "letter"),
                ("adverse_action_notice", True),
                ("contains_reason_codes", False),
            ),
        )
        with pytest.raises(SpanContractError, match="must contain reason codes"):
            validate_event(spec)


class TestParserRejectsForeignCorruption:
    def test_non_canonical_index_spelling_raises(self) -> None:
        attrs = _minimal_attrs()
        attrs[OBJ_COUNT] = 1
        attrs["at.obj.00.type"] = ObjectType.AGENT.value
        with pytest.raises(SpanContractError, match="non-canonical index"):
            parse_event(attrs)


class TestGovernanceCensus:
    @pytest.mark.parametrize("event_type", list(EventType))
    def test_census_is_sorted_and_qualified(self, event_type: EventType) -> None:
        census = governance_census(event_type)
        assert census == tuple(sorted(census))
        assert all(key.startswith("at.") for key in census)
        assert EVENT_TYPE in census

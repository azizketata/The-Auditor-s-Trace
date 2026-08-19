"""The object-scoped DECLARE analogues, including the STD.policy_version_current
instantiation of `object_absence` that V8's ground truth binds to.

The mini_log fixture is never mutated: variants are rebuilt via
``dataclasses.replace`` on copies of its events (the pinned golden log_hash
guards the fixture itself).
"""

from __future__ import annotations

import dataclasses

from auditors_trace.constraints.ruleset import LegalReference, Rule, Severity
from auditors_trace.constraints.standard import (
    object_absence,
    object_existence,
    synchronised_precedence,
    synchronised_response,
)
from auditors_trace.model.log import OCELLog, OCELRelation
from auditors_trace.model.ocel_schema import Qualifier


def _rule(constraint_id: str, template: str, **params: object) -> Rule:
    return Rule(
        constraint_id=constraint_id,
        template=template,
        description="test rule",
        severity=Severity.MEDIUM,
        params=dict(params),
        legal_basis=(
            LegalReference(
                instrument="Regulation (EU) 2024/1689",
                article="72",
                requirement="test requirement",
            ),
        ),
    )


def _std_rule() -> Rule:
    """The shipped STD.policy_version_current instantiation."""
    return _rule(
        "STD.policy_version_current",
        "object_absence",
        anchor_event_type="make_decision",
        qualifier="governed_by",
        object_type="PolicyVersion",
        attribute="effective_to",
        violating_when="non_empty",
    )


def _repoint_governed_by(log: OCELLog, event_id: str, new_policy: str) -> OCELLog:
    """A copy of the log with one event's governed_by target swapped."""
    events = []
    for event in log.events:
        if event.event_id == event_id:
            relations = tuple(
                OCELRelation(object_id=new_policy, qualifier=r.qualifier)
                if r.qualifier is Qualifier.GOVERNED_BY
                else r
                for r in event.relations
            )
            event = dataclasses.replace(event, relations=relations)
        events.append(event)
    return OCELLog(events=tuple(events), objects=log.objects, o2o=log.o2o)


class TestStdPolicyVersionCurrent:
    def test_std_flags_superseded_policy(self, mini_log: OCELLog) -> None:
        faulted = _repoint_governed_by(mini_log, "EV-A-09", "POL-CS-2025-11")
        violations = object_absence(faulted, _std_rule())
        assert len(violations) == 1
        violation = violations[0]
        assert violation.constraint_id == "STD.policy_version_current"
        assert violation.ocel_event_ids == ("EV-A-09",)  # the V8 anchor
        assert violation.ocel_object_ids == ("POL-CS-2025-11",)
        assert violation.session_id == "SES-A"
        assert "effective_to" in violation.detail

    def test_std_silent_on_current_policy(self, mini_log: OCELLog) -> None:
        assert object_absence(mini_log, _std_rule()) == []

    def test_std_ignores_pure_declarations(self, mini_log: OCELLog) -> None:
        """POL-CS-2025-11 is superseded AND present in the log — but only via
        `declares` at session_start, which is materialisation, not governance."""
        assert object_absence(mini_log, _std_rule()) == []

    def test_std_anchors_on_make_decision_only(self, mini_log: OCELLog) -> None:
        """A session_start governing a superseded policy is out of scope: V8
        repoints only make_decision, and the ground truth cites only it."""
        faulted = _repoint_governed_by(mini_log, "EV-B-01", "POL-CS-2025-11")
        assert object_absence(faulted, _std_rule()) == []

    def test_absence_always_is_classic_declare_absence(self, mini_log: OCELLog) -> None:
        rule = _rule(
            "STD.no_approval_context",
            "object_absence",
            anchor_event_type="make_decision",
            qualifier="concerns",
            object_type="Approval",
            violating_when="always",
        )
        violations = object_absence(mini_log, rule)
        assert [v.ocel_event_ids for v in violations] == [("EV-A-09",), ("EV-B-06",)]
        assert [v.ocel_object_ids for v in violations] == [("APRV-A",), ("APRV-B",)]


class TestObjectExistence:
    def test_every_decision_derives_from_an_application(self, mini_log: OCELLog) -> None:
        rule = _rule(
            "STD.decision_provenance",
            "object_existence",
            anchor_object_type="CreditDecision",
            related_object_type="Application",
            o2o_qualifier="derived_from",
        )
        assert object_existence(mini_log, rule) == []

    def test_missing_o2o_edge_is_flagged_at_the_first_citing_event(self, mini_log: OCELLog) -> None:
        without_edge = OCELLog(
            events=mini_log.events,
            objects=mini_log.objects,
            o2o=tuple(
                rel
                for rel in mini_log.o2o
                if not (rel.qualifier is Qualifier.DERIVED_FROM and rel.source_id == "DEC-B")
            ),
        )
        rule = _rule(
            "STD.decision_provenance",
            "object_existence",
            anchor_object_type="CreditDecision",
            related_object_type="Application",
            o2o_qualifier="derived_from",
        )
        violations = object_existence(without_edge, rule)
        assert len(violations) == 1
        # EV-B-04 (09:00:08) is chronologically the first event citing DEC-B.
        assert violations[0].ocel_event_ids == ("EV-B-04",)
        assert violations[0].ocel_object_ids == ("DEC-B",)

    def test_incoming_direction(self, mini_log: OCELLog) -> None:
        rule = _rule(
            "STD.application_outcome",
            "object_existence",
            anchor_object_type="Application",
            related_object_type="CreditDecision",
            o2o_qualifier="derived_from",
            direction="incoming",
        )
        assert object_existence(mini_log, rule) == []


class TestSynchronisedResponse:
    def test_request_answered_by_grant(self, mini_log: OCELLog) -> None:
        """APRV-B is requested but answered by deny_approval, not grant — the
        response (request_approval -> grant_approval on the shared Approval)
        is violated exactly there, on the unmodified fixture."""
        rule = _rule(
            "STD.request_grant_response",
            "synchronised_response",
            activity_a="request_approval",
            activity_b="grant_approval",
            object_type="Approval",
        )
        violations = synchronised_response(mini_log, rule)
        assert len(violations) == 1
        assert violations[0].ocel_event_ids == ("EV-B-04",)
        assert violations[0].ocel_object_ids == ("APRV-B",)

    def test_satisfied_response_is_silent(self, mini_log: OCELLog) -> None:
        rule = _rule(
            "STD.request_decision_response",
            "synchronised_response",
            activity_a="request_approval",
            activity_b="make_decision",
            object_type="CreditDecision",
        )
        assert synchronised_response(mini_log, rule) == []


class TestSynchronisedPrecedence:
    def test_decision_without_prior_reasoning_is_flagged(self, mini_log: OCELLog) -> None:
        """Session B never emits reasoning, so DEC-B's make_decision lacks the
        preceding emit_reasoning on the shared decision — on the unmodified
        fixture."""
        rule = _rule(
            "STD.reasoned_decision_precedence",
            "synchronised_precedence",
            activity_a="emit_reasoning",
            activity_b="make_decision",
            object_type="CreditDecision",
        )
        violations = synchronised_precedence(mini_log, rule)
        assert len(violations) == 1
        assert violations[0].ocel_event_ids == ("EV-B-06",)
        assert violations[0].ocel_object_ids == ("DEC-B",)

    def test_satisfied_precedence_is_silent(self, mini_log: OCELLog) -> None:
        rule = _rule(
            "STD.requested_approval_precedence",
            "synchronised_precedence",
            activity_a="request_approval",
            activity_b="grant_approval",
            object_type="Approval",
        )
        assert synchronised_precedence(mini_log, rule) == []

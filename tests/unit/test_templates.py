"""Per-template acceptance tests on hand-crafted mini logs (BUILD-PLAN §9 P5).

Covers T1, T4, T5, T2 (built in that order; T3 lives in
``test_templates_t3.py`` so the pre-declared descope rung stays clean).
Each flags-case asserts the exact citation the ground-truth labels
pre-registered per fault class — the `(constraint_id, event_ids)` join is
exact-match, so anchor drift here means recall loss there.
"""

from __future__ import annotations

import dataclasses

import pytest

from auditors_trace.constraints.ruleset import LegalReference, Rule, Severity
from auditors_trace.constraints.templates import (
    Violation,
    t1_synchronised_approval,
    t2_mandatory_data_coverage,
    t4_reason_code_presence,
    t5_prohibited_attribute_access,
)
from auditors_trace.model.log import OCELEvent, OCELLog, OCELModelError, OCELObject, OCELRelation
from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier

Q = Qualifier
OT = ObjectType
ET = EventType


def _obj(oid: str, otype: ObjectType, **attrs: object) -> OCELObject:
    return OCELObject(
        object_id=oid,
        object_type=otype,
        attributes=tuple(sorted(attrs.items())),  # type: ignore[arg-type]
    )


def _ev(
    eid: str,
    etype: EventType,
    time: str,
    rels: list[tuple[str, Qualifier]],
    **attrs: object,
) -> OCELEvent:
    return OCELEvent(
        event_id=eid,
        event_type=etype,
        timestamp=time,
        attributes=tuple(sorted(attrs.items())),  # type: ignore[arg-type]
        relations=tuple(OCELRelation(object_id=o, qualifier=q) for o, q in rels),
    )


def _rule(constraint_id: str, template: str, severity: Severity, **params: object) -> Rule:
    return Rule(
        constraint_id=constraint_id,
        template=template,
        description="test rule",
        severity=severity,
        params=dict(params),
        legal_basis=(
            LegalReference(
                instrument="Regulation (EU) 2024/1689",
                article="14",
                requirement="test requirement",
            ),
        ),
    )


def _t1_rule(
    allowed_roles: tuple[str, ...] = ("credit_officer",),
    decision_outcomes: tuple[str, ...] = ("grant", "deny"),
) -> Rule:
    return _rule(
        "T1.synchronised_approval",
        "t1_synchronised_approval",
        Severity.HIGH,
        allowed_roles=list(allowed_roles),
        decision_outcomes=list(decision_outcomes),
    )


def _t2_rule(required_sources: tuple[str, ...]) -> Rule:
    return _rule(
        "T2.mandatory_data_coverage",
        "t2_mandatory_data_coverage",
        Severity.MEDIUM,
        required_sources=list(required_sources),
    )


def _t4_rule(adverse_outcomes: tuple[str, ...] = ("deny", "refer")) -> Rule:
    return _rule(
        "T4.reason_code_presence",
        "t4_reason_code_presence",
        Severity.MEDIUM,
        adverse_outcomes=list(adverse_outcomes),
    )


def _t5_rule(prohibited: tuple[str, ...] = ("special_category",)) -> Rule:
    return _rule(
        "T5.prohibited_attribute_access",
        "t5_prohibited_attribute_access",
        Severity.HIGH,
        prohibited_classifications=list(prohibited),
    )


def _session_log(
    *,
    approval: str = "valid",
    approver_role: str = "credit_officer",
    outcome: str = "grant",
    approval_time: str = "2026-03-02T09:00:04Z",
    object_reason_codes: tuple[str, ...] = ("RC01",),
    with_explains: bool = True,
    resources: tuple[tuple[str, str, str, str], ...] = (),
    resource_qualifier: Qualifier = Q.READS,
    resource_concerns_app: bool = True,
) -> OCELLog:
    """One crafted session with surgical control over each template's inputs.

    ``approval``: "valid" | "absent" | "wrong_decision" | "tie".
    ``resources``: (resource id, resource_type, classification, lawful_basis)
    per retrieve_data event to emit.
    """
    adverse = outcome != "grant"
    objects: list[OCELObject] = [
        _obj("SES-1", OT.SESSION, session_id="SES-1", workflow_name="credit_scoring"),
        _obj(
            "APP-1",
            OT.APPLICATION,
            application_id="APP-1",
            product_type="consumer_loan",
            amount=4000,
            currency="DM",
            term_months=12,
            purpose="furniture",
            submitted_at="2026-03-02T08:55:00Z",
        ),
        _obj(
            "CUST-1",
            OT.APPLICANT,
            applicant_id="CUST-1",
            jurisdiction="DE",
            pseudonym_scheme="sha256_salted",
        ),
        _obj(
            "AGT-1",
            OT.AGENT,
            agent_id="AGT-1",
            name="intake",
            role="intake",
            version="1.0.0",
            framework="langgraph",
        ),
        _obj(
            "POL-CUR",
            OT.POLICY_VERSION,
            policy_id="POL-CREDIT",
            version="2026.02",
            effective_from="2026-02-01T00:00:00Z",
            effective_to="",
        ),
        _obj(
            "DEC-1",
            OT.CREDIT_DECISION,
            decision_id="DEC-1",
            outcome=outcome,
            score=520,
            reason_codes=object_reason_codes,
            band="C",
            decided_at="2026-03-02T09:00:06Z",
        ),
    ]
    events: list[OCELEvent] = [
        _ev(
            "EV-01",
            ET.SESSION_START,
            "2026-03-02T09:00:00Z",
            [
                ("SES-1", Q.DECLARES),
                ("APP-1", Q.DECLARES),
                ("CUST-1", Q.DECLARES),
                ("POL-CUR", Q.GOVERNED_BY),
            ],
            scenario_name="credit_scoring",
            seed=7,
        ),
        _ev(
            "EV-02",
            ET.INVOKE_AGENT,
            "2026-03-02T09:00:01Z",
            [
                ("AGT-1", Q.PERFORMED_BY),
                ("APP-1", Q.CONCERNS),
                ("SES-1", Q.WITHIN),
            ],
            agent_role="intake",
            step_index=1,
        ),
    ]

    time = 2
    for rid, resource_type, classification, lawful_basis in resources:
        objects.append(
            _obj(
                rid,
                OT.DATA_RESOURCE,
                resource_id=rid,
                resource_type=resource_type,
                classification=classification,
                lawful_basis=lawful_basis,
                controller="Test Controller",
                retention_class="std_7y",
            )
        )
        rels: list[tuple[str, Qualifier]] = [
            (rid, resource_qualifier),
            ("AGT-1", Q.PERFORMED_BY),
            ("SES-1", Q.WITHIN),
        ]
        if resource_concerns_app:
            rels.insert(1, ("APP-1", Q.CONCERNS))
        events.append(
            _ev(
                f"EV-R{time:02d}",
                ET.RETRIEVE_DATA,
                f"2026-03-02T09:00:{time:02d}Z",
                rels,
                purpose="credit_assessment",
                record_count=1,
                resource_type=resource_type,
            )
        )
        time += 1

    if approval != "absent":
        objects.append(
            _obj(
                "HUM-1",
                OT.HUMAN_APPROVER,
                approver_id="HUM-1",
                role=approver_role,
                authority_level=1,
            )
        )
        objects.append(
            _obj(
                "APRV-1",
                OT.APPROVAL,
                approval_id="APRV-1",
                approver_role=approver_role,
                granted=True,
                decided_at=approval_time,
            )
        )
        approves_target = "DEC-1"
        if approval == "wrong_decision":
            approves_target = "DEC-OTHER"
            objects.append(
                _obj(
                    "DEC-OTHER",
                    OT.CREDIT_DECISION,
                    decision_id="DEC-OTHER",
                    outcome="grant",
                    score=700,
                    reason_codes=(),
                    band="A",
                    decided_at="2026-03-02T08:59:00Z",
                )
            )
        if approval == "tie":
            approval_time = "2026-03-02T09:00:06Z"  # equal to the decision second
        events.append(
            _ev(
                "EV-APR",
                ET.REQUEST_APPROVAL,
                "2026-03-02T09:00:03Z",
                [
                    ("APRV-1", Q.REQUESTS),
                    ("HUM-1", Q.CONCERNS),
                    ("DEC-1", Q.CONCERNS),
                    ("APP-1", Q.CONCERNS),
                    ("AGT-1", Q.PERFORMED_BY),
                    ("SES-1", Q.WITHIN),
                ],
                amount_dm=4000,
                proposed_outcome=outcome,
                proposed_score=520,
                required_role=approver_role,
            )
        )
        events.append(
            _ev(
                "EV-GRANT",
                ET.GRANT_APPROVAL,
                approval_time,
                [
                    ("APRV-1", Q.PRODUCES),
                    ("HUM-1", Q.PERFORMED_BY),
                    (approves_target, Q.APPROVES),
                    ("APP-1", Q.CONCERNS),
                    ("SES-1", Q.WITHIN),
                ],
                approver_role=approver_role,
                authority_level=1,
                rationale_hash="ab12cd34ef567890",
            )
        )

    events.append(
        _ev(
            "EV-DEC",
            ET.MAKE_DECISION,
            "2026-03-02T09:00:06Z",
            [
                ("DEC-1", Q.PRODUCES),
                ("APP-1", Q.CONCERNS),
                ("CUST-1", Q.CONCERNS),
                ("POL-CUR", Q.GOVERNED_BY),
                ("AGT-1", Q.PERFORMED_BY),
                ("SES-1", Q.WITHIN),
            ],
            adverse=adverse,
            outcome=outcome,
            # The V6 disclosure: the EVENT attribute stays populated even when
            # the object attribute is blanked; T4 must read the object.
            reason_codes=("RC01",) if adverse else (),
            score=520,
        )
    )
    if with_explains:
        events.append(
            _ev(
                "EV-EXPL",
                ET.EMIT_REASONING,
                "2026-03-02T09:00:07Z",
                [
                    ("DEC-1", Q.EXPLAINS),
                    ("AGT-1", Q.PERFORMED_BY),
                    ("APP-1", Q.CONCERNS),
                    ("SES-1", Q.WITHIN),
                ],
                reasoning_kind="scoring_rationale",
                text_hash="0f2e6c1a9b8d7345",
            )
        )
    return OCELLog(events=tuple(events), objects=tuple(objects))


def _only(violations: list[Violation]) -> Violation:
    assert len(violations) == 1, violations
    return violations[0]


class TestT1:
    def test_t1_flags_missing_approval(self) -> None:
        log = _session_log(approval="absent")
        violation = _only(t1_synchronised_approval(log, _t1_rule()))
        assert violation.constraint_id == "T1.synchronised_approval"
        assert violation.ocel_event_ids == ("EV-DEC",)  # the V1 anchor
        assert violation.ocel_object_ids == ("DEC-1",)
        assert violation.session_id == "SES-1"
        assert violation.severity is Severity.HIGH

    def test_t1_flags_wrong_decision_approval(self) -> None:
        log = _session_log(approval="wrong_decision")
        violation = _only(t1_synchronised_approval(log, _t1_rule()))
        # The V2 anchor: the misdirected approval AND the unendorsed decision,
        # chronological; objects are (own decision, approves-target).
        assert violation.ocel_event_ids == ("EV-GRANT", "EV-DEC")
        assert violation.ocel_object_ids == ("DEC-1", "DEC-OTHER")

    def test_t1_flags_unauthorised_role(self) -> None:
        log = _session_log(approver_role="intern")
        violation = _only(t1_synchronised_approval(log, _t1_rule()))
        # The V3 anchor: the approval verdict alone, with its approver.
        assert violation.ocel_event_ids == ("EV-GRANT",)
        assert violation.ocel_object_ids == ("HUM-1",)

    def test_t1_passes_valid_approval(self, mini_log: OCELLog) -> None:
        rule = _t1_rule(allowed_roles=("senior_credit_officer",))
        assert t1_synchronised_approval(mini_log, rule) == []
        assert t1_synchronised_approval(_session_log(), _t1_rule()) == []

    def test_t1_silent_on_refer_outcomes(self) -> None:
        """The D2 near-miss: refer needs no approval — even with none present."""
        log = _session_log(approval="absent", outcome="refer")
        assert t1_synchronised_approval(log, _t1_rule()) == []

    def test_t1_silent_on_any_roster_role(self) -> None:
        """The D1 near-miss: an unusual but in-roster approver is compliant."""
        log = _session_log(approver_role="risk_manager")
        rule = _t1_rule(allowed_roles=("credit_officer", "senior_underwriter", "risk_manager"))
        assert t1_synchronised_approval(log, rule) == []

    def test_t1_requires_strictly_prior_approval(self) -> None:
        """An approval in the same whole second as the decision is not prior."""
        log = _session_log(approval="tie")
        violation = _only(t1_synchronised_approval(log, _t1_rule()))
        # Citation pair in (timestamp, event_id) order (equal second: id sorts).
        assert violation.ocel_event_ids == ("EV-DEC", "EV-GRANT")
        assert violation.ocel_object_ids == ("DEC-1",)
        assert "prior" in violation.detail

    def test_t1_tie_verdict_is_independent_of_event_id_spelling(self) -> None:
        """Adversarial-review regression: `strictly_before` once compared
        (timestamp, event_id) tuples, so an equal-second approval counted as
        prior whenever its id sorted first — and corpus-format ids
        (EVT-SESS-NNNN-015-grant_approval < ...-020-make_decision) ALWAYS
        sort the approval first, silently passing same-second approvals.
        The tie verdict must not depend on id spelling."""
        renames = {
            "EV-GRANT": "EVT-SESS-0001-015-grant_approval",
            "EV-DEC": "EVT-SESS-0001-020-make_decision",
        }
        base = _session_log(approval="tie")
        events = tuple(
            dataclasses.replace(event, event_id=renames.get(event.event_id, event.event_id))
            for event in base.events
        )
        log = OCELLog(events=events, objects=base.objects, o2o=base.o2o)
        violation = _only(t1_synchronised_approval(log, _t1_rule()))
        assert violation.ocel_event_ids == (
            "EVT-SESS-0001-015-grant_approval",
            "EVT-SESS-0001-020-make_decision",
        )
        assert "prior" in violation.detail


class TestT4:
    def test_t4_flags_empty_reason_codes(self) -> None:
        log = _session_log(outcome="deny", object_reason_codes=())
        violation = _only(t4_reason_code_presence(log, _t4_rule()))
        assert violation.constraint_id == "T4.reason_code_presence"
        assert violation.ocel_event_ids == ("EV-DEC",)  # the V6 anchor
        assert violation.ocel_object_ids == ("DEC-1",)
        assert "reason_codes" in violation.detail

    def test_t4_flags_missing_explains(self) -> None:
        """V6 variant B: codes present, but nothing explains the decision."""
        log = _session_log(outcome="deny", with_explains=False)
        violation = _only(t4_reason_code_presence(log, _t4_rule()))
        assert violation.ocel_event_ids == ("EV-DEC",)
        assert "emit_reasoning" in violation.detail

    def test_t4_emits_one_violation_when_both_arms_fail(self) -> None:
        log = _session_log(outcome="deny", object_reason_codes=(), with_explains=False)
        violation = _only(t4_reason_code_presence(log, _t4_rule()))
        assert violation.ocel_event_ids == ("EV-DEC",)
        assert "reason_codes" in violation.detail
        assert "emit_reasoning" in violation.detail

    def test_t4_ignores_grant_empty_codes(self, mini_log: OCELLog) -> None:
        """Grant decisions legitimately carry no reason codes (mini_log DEC-A);
        the refer decision DEC-B carries codes but no explains — out of scope
        when adverse_outcomes excludes refer."""
        assert t4_reason_code_presence(mini_log, _t4_rule(adverse_outcomes=("deny",))) == []

    def test_t4_reads_the_object_attribute_not_the_event(self) -> None:
        """The crafted log's EVENT attribute is populated; only the OBJECT
        attribute is blank. T4 must flag it anyway (the V6 disclosure)."""
        log = _session_log(outcome="deny", object_reason_codes=())
        assert len(t4_reason_code_presence(log, _t4_rule())) == 1


class TestT5:
    def test_t5_flags_special_category_without_basis(self) -> None:
        log = _session_log(
            resources=(("RES-DEMO", "applicant_demographics", "special_category", ""),)
        )
        violation = _only(t5_prohibited_attribute_access(log, _t5_rule()))
        assert violation.constraint_id == "T5.prohibited_attribute_access"
        assert violation.ocel_event_ids == ("EV-R02",)  # the V5 anchor
        assert violation.ocel_object_ids == ("RES-DEMO",)
        assert violation.session_id == "SES-1"

    def test_t5_passes_with_lawful_basis(self) -> None:
        log = _session_log(
            resources=(
                (
                    "RES-DEMO",
                    "applicant_demographics",
                    "special_category",
                    "Art. 10(2)(f)-(g) bias examination",
                ),
            )
        )
        assert t5_prohibited_attribute_access(log, _t5_rule()) == []

    def test_t5_whitespace_basis_counts_as_missing(self) -> None:
        log = _session_log(
            resources=(("RES-DEMO", "applicant_demographics", "special_category", "   "),)
        )
        assert len(t5_prohibited_attribute_access(log, _t5_rule())) == 1

    def test_t5_ignores_non_special_resources(self) -> None:
        log = _session_log(resources=(("RES-BUR", "credit_bureau", "internal", ""),))
        assert t5_prohibited_attribute_access(log, _t5_rule()) == []

    def test_t5_requires_application_context(self) -> None:
        """The spec scopes T5 to events concerning an Application."""
        log = _session_log(
            resources=(("RES-DEMO", "applicant_demographics", "special_category", ""),),
            resource_concerns_app=False,
        )
        assert t5_prohibited_attribute_access(log, _t5_rule()) == []

    def test_t5_call_llm_arm_is_vacuous_under_the_frozen_matrix(self) -> None:
        """Pins the documented vacuity claim: the model itself rejects a
        call_llm -> DataResource relation, so the defensive call_llm arm in
        T5 can never fire on a valid log."""
        session = _obj("SES-X", OT.SESSION, session_id="SES-X", workflow_name="credit_scoring")
        resource = _obj(
            "RES-X",
            OT.DATA_RESOURCE,
            resource_id="RES-X",
            resource_type="applicant_demographics",
            classification="special_category",
            lawful_basis="",
            controller="Test Controller",
            retention_class="std_7y",
        )
        event = _ev(
            "EV-X",
            ET.CALL_LLM,
            "2026-03-02T09:00:01Z",
            [("RES-X", Q.READS), ("SES-X", Q.WITHIN)],
            temperature=0.0,
            max_tokens=64,
            model_seed=7,
            purpose="test",
        )
        with pytest.raises(OCELModelError, match="not permitted"):
            OCELLog(events=(event,), objects=(session, resource))


class TestT2:
    def test_t2_flags_missing_required_source(self) -> None:
        log = _session_log(resources=(("RES-BUR", "credit_bureau", "internal", ""),))
        rule = _t2_rule(("credit_bureau", "income_verification", "internal_credit_history"))
        violation = _only(t2_mandatory_data_coverage(log, rule))
        assert violation.constraint_id == "T2.mandatory_data_coverage"
        assert violation.ocel_event_ids == ("EV-DEC",)  # the V4 anchor
        assert violation.ocel_object_ids == ()  # mirrors the V4 labels exactly
        assert violation.session_id == "SES-1"
        assert "income_verification" in violation.detail
        assert "internal_credit_history" in violation.detail
        assert "credit_bureau" not in violation.detail

    def test_t2_passes_full_coverage(self) -> None:
        log = _session_log(
            resources=(
                ("RES-BUR", "credit_bureau", "internal", ""),
                ("RES-INC", "income_verification", "internal", ""),
                ("RES-HIS", "internal_credit_history", "internal", ""),
            )
        )
        rule = _t2_rule(("credit_bureau", "income_verification", "internal_credit_history"))
        assert t2_mandatory_data_coverage(log, rule) == []

    def test_t2_ignores_declares_only_resource(self) -> None:
        """A declared-but-never-read resource is not coverage: the `declares`
        relation exists for pm4py materialisation, never as semantic evidence."""
        log = _session_log(
            resources=(("RES-INC", "income_verification", "internal", ""),),
            resource_qualifier=Q.DECLARES,
        )
        rule = _t2_rule(("income_verification",))
        violation = _only(t2_mandatory_data_coverage(log, rule))
        assert "income_verification" in violation.detail


class TestViolationContract:
    def test_violation_constructible_without_log(self) -> None:
        """The judge/baseline path: plain strings in, joinable output."""
        from auditors_trace.eval.metrics import confusion

        prediction = Violation(
            constraint_id="T1.synchronised_approval",
            ocel_event_ids=("EVT-SESS-0001-025-make_decision",),
        )
        assert prediction.session_id == ""
        assert prediction.severity is Severity.MEDIUM
        assert prediction.detail == ""
        assert confusion([prediction], ()).false_positives == 1

"""T3 DelegationIntegrity — built last, in its own file, because dropping T3 is
the pre-declared descope rung (PLAN-REVIEW B18): removing this file and the T3
rule is the whole rollback.

The single-violation guarantee is the load-bearing design: 10 of 15
repoint-mode V7 sessions in the live corpus produce a directed cycle in the
naive handoff graph, and a naive cycle arm would emit a duplicate violation
for each — a false positive under the exact ground-truth join. The cycle arm
therefore excludes handoffs already cited as misdirected by the orphan arm.
"""

from __future__ import annotations

from auditors_trace.constraints.ruleset import LegalReference, Rule, Severity
from auditors_trace.constraints.templates import Violation, t3_delegation_integrity
from auditors_trace.model.log import OCELEvent, OCELLog, OCELObject, OCELRelation
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


def _agent(oid: str, role: str) -> OCELObject:
    return _obj(
        oid,
        OT.AGENT,
        agent_id=oid,
        name=role,
        role=role,
        version="1.0.0",
        framework="langgraph",
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


def _t3_rule(entrypoint_roles: tuple[str, ...] = ("intake",)) -> Rule:
    return Rule(
        constraint_id="T3.delegation_integrity",
        template="t3_delegation_integrity",
        description="test rule",
        severity=Severity.MEDIUM,
        params={"entrypoint_roles": list(entrypoint_roles)},
        legal_basis=(
            LegalReference(
                instrument="Regulation (EU) 2024/1689",
                article="14",
                requirement="test requirement",
            ),
        ),
    )


Step = tuple[str, str, list[tuple[str, Qualifier]], dict[str, object]]


def _chain_log(steps: list[Step], agents: list[OCELObject]) -> OCELLog:
    """A single session from (event id, event type value, extra rels, attrs) steps."""
    objects = [
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
        *agents,
    ]
    events = [
        _ev(
            "EV-00",
            ET.SESSION_START,
            "2026-03-02T09:00:00Z",
            [("SES-1", Q.DECLARES), ("APP-1", Q.DECLARES), ("CUST-1", Q.DECLARES)],
            scenario_name="credit_scoring",
            seed=7,
        )
    ]
    for offset, (eid, etype_value, extra_rels, attrs) in enumerate(steps, start=1):
        rels = [*extra_rels, ("APP-1", Q.CONCERNS), ("SES-1", Q.WITHIN)]
        events.append(
            _ev(eid, EventType(etype_value), f"2026-03-02T09:00:{offset:02d}Z", rels, **attrs)
        )
    return OCELLog(events=tuple(events), objects=tuple(objects))


def _invoke(eid: str, agent: str, role: str) -> Step:
    return (eid, "invoke_agent", [(agent, Q.PERFORMED_BY)], {"agent_role": role, "step_index": 1})


def _handoff(eid: str, source: str, target: str) -> Step:
    return (
        eid,
        "handoff",
        [(source, Q.FROM), (target, Q.TO)],
        {"handoff_kind": "delegation", "reason": "next_stage"},
    )


def _reasoning(eid: str, agent: str) -> Step:
    return (
        eid,
        "emit_reasoning",
        [(agent, Q.PERFORMED_BY)],
        {"reasoning_kind": "note", "text_hash": "0f2e6c1a9b8d7345"},
    )


def _only(violations: list[Violation]) -> Violation:
    assert len(violations) == 1, violations
    return violations[0]


class TestOrphanArm:
    def test_t3_flags_orphan_agent_action(self) -> None:
        """V7 delete-shape: the handoff is gone, so the orphan's predecessor is
        an ordinary event and the citation is the orphaned action alone."""
        log = _chain_log(
            [
                _invoke("EV-01", "AGT-A", "intake"),
                _reasoning("EV-02", "AGT-A"),
                _invoke("EV-03", "AGT-B", "scoring"),
            ],
            [_agent("AGT-A", "intake"), _agent("AGT-B", "scoring")],
        )
        violation = _only(t3_delegation_integrity(log, _t3_rule()))
        assert violation.constraint_id == "T3.delegation_integrity"
        assert violation.ocel_event_ids == ("EV-03",)
        assert violation.ocel_object_ids == ("AGT-B",)
        assert violation.session_id == "SES-1"
        assert violation.severity is Severity.MEDIUM

    def test_t3_flags_misdirected_handoff(self) -> None:
        """V7 repoint-shape: the orphan's immediate predecessor is a handoff to
        someone else — cite handoff AND orphaned action (the V7 anchor pair)."""
        log = _chain_log(
            [
                _invoke("EV-01", "AGT-A", "intake"),
                _handoff("EV-02", "AGT-A", "AGT-C"),
                _invoke("EV-03", "AGT-B", "scoring"),
            ],
            [_agent("AGT-A", "intake"), _agent("AGT-B", "scoring"), _agent("AGT-C", "review")],
        )
        violation = _only(t3_delegation_integrity(log, _t3_rule()))
        assert violation.ocel_event_ids == ("EV-02", "EV-03")
        assert violation.ocel_object_ids == ("AGT-B",)

    def test_t3_passes_entrypoint_without_handoff(self) -> None:
        log = _chain_log(
            [_invoke("EV-01", "AGT-A", "intake"), _reasoning("EV-02", "AGT-A")],
            [_agent("AGT-A", "intake")],
        )
        assert t3_delegation_integrity(log, _t3_rule()) == []

    def test_t3_passes_clean_chain(self) -> None:
        log = _chain_log(
            [
                _invoke("EV-01", "AGT-A", "intake"),
                _handoff("EV-02", "AGT-A", "AGT-B"),
                _invoke("EV-03", "AGT-B", "scoring"),
                _reasoning("EV-04", "AGT-B"),
            ],
            [_agent("AGT-A", "intake"), _agent("AGT-B", "scoring")],
        )
        assert t3_delegation_integrity(log, _t3_rule()) == []

    def test_t3_on_mini_log_flags_the_fixture_divergence(self, mini_log: OCELLog) -> None:
        """Session A of the shared fixture has the adjudication agent deciding
        with no handoff — structurally the V7 delete-shape. Pinned here so the
        fixture's divergence from the scenario is documented, not surprising."""
        violation = _only(t3_delegation_integrity(mini_log, _t3_rule()))
        assert violation.ocel_event_ids == ("EV-A-09",)
        assert violation.ocel_object_ids == ("AGT-ADJ",)
        assert violation.session_id == "SES-A"


class TestCycleArm:
    def test_t3_flags_delegation_cycle(self) -> None:
        """A pure cycle with no orphan: both agents act after being handed to,
        but the delegation chain loops — one violation citing the cycle's
        handoffs chronologically."""
        log = _chain_log(
            [
                _invoke("EV-01", "AGT-A", "intake"),
                _handoff("EV-02", "AGT-A", "AGT-B"),
                _invoke("EV-03", "AGT-B", "scoring"),
                _handoff("EV-04", "AGT-B", "AGT-A"),
                _reasoning("EV-05", "AGT-A"),
            ],
            [_agent("AGT-A", "intake"), _agent("AGT-B", "scoring")],
        )
        violation = _only(t3_delegation_integrity(log, _t3_rule()))
        assert violation.ocel_event_ids == ("EV-02", "EV-04")
        assert violation.ocel_object_ids == ("AGT-A", "AGT-B")
        assert "cycle" in violation.detail

    def test_t3_emits_single_violation_when_repoint_creates_a_cycle(self) -> None:
        """The live-corpus shape (e.g. SESS-0062): a repointed handoff both
        orphans the next agent AND closes a from->to cycle. The cycle arm must
        exclude the already-cited handoff, leaving exactly ONE violation."""
        log = _chain_log(
            [
                _invoke("EV-01", "AGT-A", "intake"),
                _handoff("EV-02", "AGT-A", "AGT-B"),
                _invoke("EV-03", "AGT-B", "retrieval"),
                _handoff("EV-04", "AGT-B", "AGT-A"),  # repointed; was -> AGT-C
                _invoke("EV-05", "AGT-C", "scoring"),
            ],
            [_agent("AGT-A", "intake"), _agent("AGT-B", "retrieval"), _agent("AGT-C", "scoring")],
        )
        violation = _only(t3_delegation_integrity(log, _t3_rule()))
        assert violation.ocel_event_ids == ("EV-04", "EV-05")
        assert violation.ocel_object_ids == ("AGT-C",)


class TestScope:
    def test_t3_ignores_human_approver_actions(self) -> None:
        """Approval verdicts are performed by HumanApprovers, not Agents — they
        never need a handoff."""
        log = _chain_log(
            [
                _invoke("EV-01", "AGT-A", "intake"),
                (
                    "EV-02",
                    "request_approval",
                    [
                        ("APRV-1", Q.REQUESTS),
                        ("HUM-1", Q.CONCERNS),
                        ("AGT-A", Q.PERFORMED_BY),
                    ],
                    {
                        "amount_dm": 4000,
                        "proposed_outcome": "grant",
                        "proposed_score": 520,
                        "required_role": "credit_officer",
                    },
                ),
                (
                    "EV-03",
                    "grant_approval",
                    [
                        ("APRV-1", Q.PRODUCES),
                        ("HUM-1", Q.PERFORMED_BY),
                    ],
                    {
                        "approver_role": "credit_officer",
                        "authority_level": 1,
                        "rationale_hash": "ab12cd34ef567890",
                    },
                ),
            ],
            [
                _agent("AGT-A", "intake"),
                _obj(
                    "HUM-1",
                    OT.HUMAN_APPROVER,
                    approver_id="HUM-1",
                    role="credit_officer",
                    authority_level=1,
                ),
                _obj(
                    "APRV-1",
                    OT.APPROVAL,
                    approval_id="APRV-1",
                    approver_role="credit_officer",
                    granted=True,
                    decided_at="2026-03-02T09:00:03Z",
                ),
            ],
        )
        assert t3_delegation_integrity(log, _t3_rule()) == []

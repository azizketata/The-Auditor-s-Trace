"""Shared fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

#: The only fields that may legitimately differ between two identical scripted
#: runs: span wall-clock nanoseconds (real telemetry) and the LangGraph task
#: UUIDs inside the OpenInference metadata blob. Everything else is
#: byte-stable, and both the determinism suite and the golden-regeneration
#: test normalise exactly this set — nothing more.
VOLATILE_METADATA_KEYS = ("langgraph_checkpoint_ns", "checkpoint_ns")


def normalise_span_line(line: str) -> str:
    """Canonicalise one span-file line with the volatile fields zeroed."""
    row = json.loads(line)
    row["start_time_unix_nano"] = 0
    row["end_time_unix_nano"] = 0
    for event in row.get("events", []):
        event["timestamp_unix_nano"] = 0
    metadata = row["attributes"].get("metadata")
    if isinstance(metadata, str):
        # Preserve the blob's own key order (no sort_keys): only the volatile
        # keys are dropped, so an order-level difference is still caught.
        blob = json.loads(metadata)
        for key in VOLATILE_METADATA_KEYS:
            blob.pop(key, None)
        row["attributes"]["metadata"] = json.dumps(blob)
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pytest.fixture(scope="session")
def span_line_normaliser() -> Callable[[str], str]:
    """The one span-line normaliser, shared by every suite that compares runs."""
    return normalise_span_line


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The repository root, resolved from this file's location."""
    return Path(__file__).resolve().parent.parent


def build_mini_log() -> object:
    """The miniature OCEL log, model-side twin of tests/fixtures/mini_log.json.

    Two interleaved credit-scoring sessions covering all 13 event types, all
    12 object types, all three O2O qualifiers, one pure declaration (the
    superseded PolicyVersion, reachable only via ``declares``), and every
    attribute value kind: str, empty str (RES-BUREAU.lawful_basis), bool both
    ways, int, float (temperature 0.0), tuple (DEC-B.reason_codes) and empty
    tuple (DEC-A.reason_codes). Session A grants; session B is denied by the
    human approver and referred. Imports lazily so collection survives while
    model/log.py is still a stub.
    """
    from auditors_trace.model.log import OCELEvent, OCELLog, OCELObject, OCELRelation
    from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
    from auditors_trace.model.span_contract import O2ORef

    qual = Qualifier
    otype = ObjectType

    def obj(oid: str, otype: ObjectType, **attrs: object) -> OCELObject:
        return OCELObject(
            object_id=oid,
            object_type=otype,
            attributes=tuple(sorted(attrs.items())),  # type: ignore[arg-type]
        )

    def ev(
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

    objects = (
        obj("SES-A", otype.SESSION, session_id="SES-A", workflow_name="credit_scoring"),
        obj("SES-B", otype.SESSION, session_id="SES-B", workflow_name="credit_scoring"),
        obj(
            "APP-A",
            otype.APPLICATION,
            application_id="APP-A",
            product_type="consumer_loan",
            amount=4870,
            currency="DM",
            term_months=24,
            purpose="radio_tv",
            submitted_at="2026-03-02T08:55:00Z",
        ),
        obj(
            "APP-B",
            otype.APPLICATION,
            application_id="APP-B",
            product_type="consumer_loan",
            amount=9055,
            currency="DM",
            term_months=36,
            purpose="education",
            submitted_at="2026-03-02T08:57:00Z",
        ),
        obj(
            "CUST-A",
            otype.APPLICANT,
            applicant_id="CUST-A",
            jurisdiction="DE",
            pseudonym_scheme="sha256_salted",
        ),
        obj(
            "CUST-B",
            otype.APPLICANT,
            applicant_id="CUST-B",
            jurisdiction="DE",
            pseudonym_scheme="sha256_salted",
        ),
        obj(
            "AGT-INTAKE",
            otype.AGENT,
            agent_id="AGT-INTAKE",
            name="intake",
            role="intake",
            version="1.0.0",
            framework="langgraph",
        ),
        obj(
            "AGT-ADJ",
            otype.AGENT,
            agent_id="AGT-ADJ",
            name="adjudication",
            role="adjudication",
            version="1.0.0",
            framework="langgraph",
        ),
        obj(
            "POL-CS-2026-02",
            otype.POLICY_VERSION,
            policy_id="POL-CREDIT",
            version="2026.02",
            effective_from="2026-02-01T00:00:00Z",
            effective_to="",
        ),
        obj(
            "POL-CS-2025-11",
            otype.POLICY_VERSION,
            policy_id="POL-CREDIT",
            version="2025.11",
            effective_from="2025-11-01T00:00:00Z",
            effective_to="2026-01-31T23:59:59Z",
        ),
        obj(
            "MDL-HAIKU",
            otype.MODEL,
            model_id="claude-haiku-4-5",
            provider="anthropic",
            version="2025-10",
        ),
        obj(
            "PRM-INTAKE-3",
            otype.PROMPT,
            prompt_name="intake_summary",
            prompt_version="3",
            template_hash="a3f1c9d2e8b74650",
        ),
        obj("TL-SCORECARD", otype.TOOL, tool_name="scorecard", tool_type="deterministic_function"),
        obj(
            "RES-BUREAU",
            otype.DATA_RESOURCE,
            resource_id="RES-BUREAU",
            resource_type="credit_bureau",
            classification="internal",
            lawful_basis="",
            controller="bureau_gmbh",
            retention_class="std_7y",
        ),
        obj(
            "HUM-APPR-1",
            otype.HUMAN_APPROVER,
            approver_id="HUM-APPR-1",
            role="senior_credit_officer",
            authority_level=2,
        ),
        obj(
            "APRV-A",
            otype.APPROVAL,
            approval_id="APRV-A",
            approver_role="senior_credit_officer",
            granted=True,
            decided_at="2026-03-02T09:00:13Z",
        ),
        obj(
            "APRV-B",
            otype.APPROVAL,
            approval_id="APRV-B",
            approver_role="senior_credit_officer",
            granted=False,
            decided_at="2026-03-02T09:00:10Z",
        ),
        obj(
            "DEC-A",
            otype.CREDIT_DECISION,
            decision_id="DEC-A",
            outcome="grant",
            score=612,
            reason_codes=(),
            band="A",
            decided_at="2026-03-02T09:00:15Z",
        ),
        obj(
            "DEC-B",
            otype.CREDIT_DECISION,
            decision_id="DEC-B",
            outcome="refer",
            score=441,
            reason_codes=("RC03", "RC98"),
            band="D",
            decided_at="2026-03-02T09:00:12Z",
        ),
    )

    events = (
        # --- Session A: full grant path ---
        ev(
            "EV-A-01",
            EventType.SESSION_START,
            "2026-03-02T09:00:00Z",
            [
                ("SES-A", qual.DECLARES),
                ("APP-A", qual.DECLARES),
                ("CUST-A", qual.DECLARES),
                ("POL-CS-2026-02", qual.GOVERNED_BY),
                ("POL-CS-2025-11", qual.DECLARES),  # the pure declaration
            ],
            scenario_name="credit_scoring",
            seed=42,
        ),
        ev(
            "EV-A-02",
            EventType.INVOKE_AGENT,
            "2026-03-02T09:00:01Z",
            [
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("APP-A", qual.CONCERNS),
                ("SES-A", qual.WITHIN),
            ],
            agent_role="intake",
            step_index=1,
        ),
        ev(
            "EV-A-03",
            EventType.CALL_LLM,
            "2026-03-02T09:00:03Z",
            [
                ("MDL-HAIKU", qual.USES),
                ("PRM-INTAKE-3", qual.USES),
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("APP-A", qual.CONCERNS),
                ("SES-A", qual.WITHIN),
            ],
            temperature=0.0,
            max_tokens=1024,
            model_seed=42,
            purpose="intake_summary",
        ),
        ev(
            "EV-A-04",
            EventType.CALL_TOOL,
            "2026-03-02T09:00:05Z",
            [
                ("TL-SCORECARD", qual.USES),
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("APP-A", qual.CONCERNS),
                ("SES-A", qual.WITHIN),
            ],
            status="ok",
            tool_call_id="TC-A-01",
        ),
        ev(
            "EV-A-05",
            EventType.RETRIEVE_DATA,
            "2026-03-02T09:00:07Z",
            [
                ("RES-BUREAU", qual.READS),
                ("APP-A", qual.CONCERNS),
                ("CUST-A", qual.CONCERNS),
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("SES-A", qual.WITHIN),
            ],
            purpose="credit_assessment",
            record_count=1,
            resource_type="credit_bureau",
        ),
        ev(
            "EV-A-06",
            EventType.EMIT_REASONING,
            "2026-03-02T09:00:09Z",
            [
                ("DEC-A", qual.EXPLAINS),
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("APP-A", qual.CONCERNS),
                ("SES-A", qual.WITHIN),
            ],
            reasoning_kind="scoring_rationale",
            text_hash="0f2e6c1a9b8d7345",
        ),
        ev(
            "EV-A-07",
            EventType.REQUEST_APPROVAL,
            "2026-03-02T09:00:11Z",
            [
                ("APRV-A", qual.REQUESTS),
                ("HUM-APPR-1", qual.CONCERNS),
                ("DEC-A", qual.CONCERNS),
                ("APP-A", qual.CONCERNS),
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("SES-A", qual.WITHIN),
            ],
            amount_dm=4870,
            proposed_outcome="grant",
            proposed_score=612,
            required_role="senior_credit_officer",
        ),
        ev(
            "EV-A-08",
            EventType.GRANT_APPROVAL,
            "2026-03-02T09:00:13Z",
            [
                ("APRV-A", qual.PRODUCES),
                ("HUM-APPR-1", qual.PERFORMED_BY),
                ("DEC-A", qual.APPROVES),
                ("APP-A", qual.CONCERNS),
                ("SES-A", qual.WITHIN),
            ],
            approver_role="senior_credit_officer",
            authority_level=2,
            rationale_hash="5d4c3b2a19087f6e",
        ),
        ev(
            "EV-A-09",
            EventType.MAKE_DECISION,
            "2026-03-02T09:00:15Z",
            [
                ("DEC-A", qual.PRODUCES),
                ("APP-A", qual.CONCERNS),
                ("CUST-A", qual.CONCERNS),
                ("POL-CS-2026-02", qual.GOVERNED_BY),
                ("APRV-A", qual.CONCERNS),
                ("AGT-ADJ", qual.PERFORMED_BY),
                ("SES-A", qual.WITHIN),
            ],
            adverse=False,
            outcome="grant",
            reason_codes=(),
            score=612,
        ),
        ev(
            "EV-A-10",
            EventType.NOTIFY_APPLICANT,
            "2026-03-02T09:00:17Z",
            [
                ("CUST-A", qual.CONCERNS),
                ("DEC-A", qual.CONCERNS),
                ("APP-A", qual.CONCERNS),
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("SES-A", qual.WITHIN),
            ],
            adverse_action_notice=False,
            channel="email",
            contains_reason_codes=False,
        ),
        ev(
            "EV-A-11",
            EventType.SESSION_END,
            "2026-03-02T09:00:19Z",
            [
                ("SES-A", qual.WITHIN),
                ("APP-A", qual.CONCERNS),
                ("DEC-A", qual.CONCERNS),
            ],
            duration_seconds=19,
            event_count=11,
            outcome="grant",
        ),
        # --- Session B: handoff, human denial, refer ---
        ev(
            "EV-B-01",
            EventType.SESSION_START,
            "2026-03-02T09:00:02Z",
            [
                ("SES-B", qual.DECLARES),
                ("APP-B", qual.DECLARES),
                ("CUST-B", qual.DECLARES),
                ("POL-CS-2026-02", qual.GOVERNED_BY),
            ],
            scenario_name="credit_scoring",
            seed=42,
        ),
        ev(
            "EV-B-02",
            EventType.INVOKE_AGENT,
            "2026-03-02T09:00:04Z",
            [
                ("AGT-INTAKE", qual.PERFORMED_BY),
                ("APP-B", qual.CONCERNS),
                ("SES-B", qual.WITHIN),
            ],
            agent_role="intake",
            step_index=1,
        ),
        ev(
            "EV-B-03",
            EventType.HANDOFF,
            "2026-03-02T09:00:06Z",
            [
                ("AGT-INTAKE", qual.FROM),
                ("AGT-ADJ", qual.TO),
                ("APP-B", qual.CONCERNS),
                ("SES-B", qual.WITHIN),
            ],
            handoff_kind="escalation",
            reason="adjudication_required",
        ),
        ev(
            "EV-B-04",
            EventType.REQUEST_APPROVAL,
            "2026-03-02T09:00:08Z",
            [
                ("APRV-B", qual.REQUESTS),
                ("HUM-APPR-1", qual.CONCERNS),
                ("DEC-B", qual.CONCERNS),
                ("APP-B", qual.CONCERNS),
                ("AGT-ADJ", qual.PERFORMED_BY),
                ("SES-B", qual.WITHIN),
            ],
            amount_dm=9055,
            proposed_outcome="deny",
            proposed_score=441,
            required_role="senior_credit_officer",
        ),
        ev(
            "EV-B-05",
            EventType.DENY_APPROVAL,
            "2026-03-02T09:00:10Z",
            [
                ("APRV-B", qual.PRODUCES),
                ("HUM-APPR-1", qual.PERFORMED_BY),
                ("DEC-B", qual.CONCERNS),  # a denial concerns, never approves
                ("APP-B", qual.CONCERNS),
                ("SES-B", qual.WITHIN),
            ],
            approver_role="senior_credit_officer",
            authority_level=2,
            override_reason="insufficient_collateral_evidence",
            rationale_hash="7b6a5948e3d2c1f0",
        ),
        ev(
            "EV-B-06",
            EventType.MAKE_DECISION,
            "2026-03-02T09:00:12Z",
            [
                ("DEC-B", qual.PRODUCES),
                ("APP-B", qual.CONCERNS),
                ("CUST-B", qual.CONCERNS),
                ("POL-CS-2026-02", qual.GOVERNED_BY),
                ("APRV-B", qual.CONCERNS),
                ("AGT-ADJ", qual.PERFORMED_BY),
                ("SES-B", qual.WITHIN),
            ],
            adverse=True,
            outcome="refer",
            reason_codes=("RC03", "RC98"),
            score=441,
        ),
        ev(
            "EV-B-07",
            EventType.SESSION_END,
            "2026-03-02T09:00:14Z",
            [
                ("SES-B", qual.WITHIN),
                ("APP-B", qual.CONCERNS),
                ("DEC-B", qual.CONCERNS),
            ],
            duration_seconds=12,
            event_count=7,
            outcome="refer",
        ),
    )

    o2o = (
        O2ORef(source_id="APP-A", target_id="CUST-A", qualifier=qual.SUBMITTED_BY),
        O2ORef(source_id="APP-B", target_id="CUST-B", qualifier=qual.SUBMITTED_BY),
        O2ORef(source_id="DEC-A", target_id="APP-A", qualifier=qual.DERIVED_FROM),
        O2ORef(source_id="DEC-B", target_id="APP-B", qualifier=qual.DERIVED_FROM),
        O2ORef(source_id="AGT-INTAKE", target_id="AGT-ADJ", qualifier=qual.DELEGATES_TO),
    )

    return OCELLog(events=events, objects=objects, o2o=o2o)


@pytest.fixture(scope="session")
def mini_log() -> object:
    """Session-scoped miniature log (immutable, safe to share)."""
    return build_mini_log()


@pytest.fixture(scope="session")
def mini_log_builder() -> Callable[[], object]:
    """The builder itself, for tests that need independent reconstructions."""
    return build_mini_log

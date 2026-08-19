"""Violation-to-record rendering on the mini fixture (Phase 6).

The renderer never re-queries the log: everything it cites comes from the
prebuilt RenderIndex (span sidecar + per-session context) and the two
hand-authored sources (ruleset, crosswalk). These tests pin the derivation
rules: semantic qualifiers only, governed_by policy, one trace per record.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auditors_trace.constraints.ruleset import RuleSet, Severity, load_ruleset
from auditors_trace.constraints.templates import Violation
from auditors_trace.evidence.chain import sha256_bytes, verify_chain
from auditors_trace.evidence.crosswalk import Crosswalk, load_crosswalk
from auditors_trace.evidence.renderer import (
    CrossTraceEvidenceError,
    RenderError,
    RenderIndex,
    UnknownRuleError,
    UnmappedEventError,
    build_render_index,
    render,
    render_all,
)
from auditors_trace.ingest.mapper import EventSpanRef, SessionSpanRef, SpanIndex
from auditors_trace.model.log import OCELLog, log_hash

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURE_CROSSWALK = REPO / "tests" / "fixtures" / "crosswalk_fixture.yaml"
FIXTURE_CROSSWALK_SHA256 = sha256_bytes(FIXTURE_CROSSWALK.read_bytes())

TRACE_A = "a" * 32
TRACE_B = "b" * 32


@pytest.fixture(scope="module")
def ruleset() -> RuleSet:
    return load_ruleset(REPO / "rules" / "rules.yaml")


@pytest.fixture(scope="module")
def crosswalk(ruleset: RuleSet) -> Crosswalk:
    return load_crosswalk(
        FIXTURE_CROSSWALK, required_ids={rule.constraint_id for rule in ruleset.rules}
    )


def _synthetic_span_index(log: OCELLog) -> SpanIndex:
    """A deterministic sidecar twin for the mini log (which has none)."""
    events = tuple(
        EventSpanRef(
            event_id=event.event_id,
            trace_id=TRACE_A if "-A-" in event.event_id else TRACE_B,
            span_id=f"{position:016x}",
            enrichment_span_ids=(),
        )
        for position, event in enumerate(log.events, start=1)
    )
    sessions = (
        SessionSpanRef(
            session_id="SES-A", trace_id=TRACE_A, root_span_id="f" * 16, session_span_ids=()
        ),
        SessionSpanRef(
            session_id="SES-B", trace_id=TRACE_B, root_span_id="e" * 16, session_span_ids=()
        ),
    )
    return SpanIndex(schema_version=1, sessions=sessions, events=events)


@pytest.fixture(scope="module")
def render_index(mini_log: OCELLog) -> RenderIndex:
    return build_render_index(mini_log, _synthetic_span_index(mini_log))


def _violation(
    constraint_id: str = "T1.synchronised_approval",
    event_ids: tuple[str, ...] = ("EV-A-09",),
    object_ids: tuple[str, ...] = ("DEC-A",),
    session_id: str = "SES-A",
    severity: Severity = Severity.HIGH,
) -> Violation:
    return Violation(
        constraint_id=constraint_id,
        ocel_event_ids=event_ids,
        ocel_object_ids=object_ids,
        session_id=session_id,
        severity=severity,
    )


class TestRenderIndex:
    def test_session_context_derivation(self, render_index: RenderIndex) -> None:
        context_a = render_index.session_context["SES-A"]
        # governed_by on the make_decision — never the declared-only 2025.11.
        assert context_a.policy_version == "2026.02"
        assert context_a.model_versions == {"claude-haiku-4-5": "2025-10"}
        assert context_a.agent_versions == {"AGT-INTAKE": "1.0.0", "AGT-ADJ": "1.0.0"}

        context_b = render_index.session_context["SES-B"]
        assert context_b.policy_version == "2026.02"
        assert context_b.model_versions == {}  # session B never calls a model
        # from/to on the handoff and performed_by both count; the HumanApprover
        # performing the deny_approval is not an Agent and never appears.
        assert context_b.agent_versions == {"AGT-INTAKE": "1.0.0", "AGT-ADJ": "1.0.0"}

    def test_every_event_is_span_mapped(self, mini_log: OCELLog, render_index: RenderIndex) -> None:
        assert set(render_index.event_span) == {event.event_id for event in mini_log.events}


class TestSpanIndexBinding:
    """Adversarial-review regression (19 Aug 2026): a stale or cross-split
    sidecar rendered forged span citations with exit 0. The sidecar must
    carry exactly this log's event and session identity."""

    def test_foreign_event_set_rejected(self, mini_log: OCELLog) -> None:
        from auditors_trace.evidence.renderer import SpanIndexMismatchError

        index = _synthetic_span_index(mini_log)
        truncated = SpanIndex(schema_version=1, sessions=index.sessions, events=index.events[:-1])
        with pytest.raises(SpanIndexMismatchError, match="does not belong"):
            build_render_index(mini_log, truncated)

        renamed = SpanIndex(
            schema_version=1,
            sessions=index.sessions,
            events=(
                *index.events[:-1],
                EventSpanRef(
                    event_id="EVT-FOREIGN-001",
                    trace_id=TRACE_A,
                    span_id="0000000000000099",
                    enrichment_span_ids=(),
                ),
            ),
        )
        with pytest.raises(SpanIndexMismatchError, match="does not belong"):
            build_render_index(mini_log, renamed)

    def test_foreign_session_set_rejected(self, mini_log: OCELLog) -> None:
        from auditors_trace.evidence.renderer import SpanIndexMismatchError

        index = _synthetic_span_index(mini_log)
        wrong_sessions = SpanIndex(
            schema_version=1,
            sessions=(
                SessionSpanRef(
                    session_id="SESS-FOREIGN",
                    trace_id=TRACE_A,
                    root_span_id="f" * 16,
                    session_span_ids=(),
                ),
            ),
            events=index.events,
        )
        with pytest.raises(SpanIndexMismatchError, match="Session"):
            build_render_index(mini_log, wrong_sessions)


class TestRender:
    def test_record_assembly(
        self,
        mini_log: OCELLog,
        render_index: RenderIndex,
        crosswalk: Crosswalk,
        ruleset: RuleSet,
    ) -> None:
        input_hash = log_hash(mini_log)
        record = render(
            _violation(),
            crosswalk=crosswalk,
            ruleset=ruleset,
            render_index=render_index,
            input_log_sha256=input_hash,
            crosswalk_sha256=FIXTURE_CROSSWALK_SHA256,
        )
        assert record.constraint.id == "T1.synchronised_approval"
        assert record.constraint.ruleset_version == ruleset.ruleset_version
        assert "grant_approval" in record.constraint.formal  # from the ruleset
        assert record.constraint.natural_language  # from the crosswalk
        assert {basis.article for basis in record.legal_basis} == {"14", "26"}
        assert record.standard_refs
        assert record.severity is Severity.HIGH
        assert record.evidence.ocel_event_ids == ("EV-A-09",)
        assert record.evidence.otel_trace_id == TRACE_A
        assert len(record.evidence.otel_span_ids) == 1
        assert record.context.policy_version == "2026.02"
        assert record.provenance.engine_version == "engine/0.1.0"
        assert record.provenance.engine_commit == ""
        assert record.provenance.input_log_sha256 == input_hash
        assert record.reproducibility.rerun_command == (
            f"python -m auditors_trace.cli check --log {input_hash} "
            f"--rules {ruleset.ruleset_version} "
            f"--span-index-sha256 {render_index.span_index_sha256} "
            f"--crosswalk-sha256 {FIXTURE_CROSSWALK_SHA256}"
        )
        from auditors_trace.evidence.chain import violation_id as violation_id_fn

        assert record.violation_id == violation_id_fn(
            record.constraint.id, record.evidence.ocel_event_ids, input_hash
        )
        assert record.integrity is None
        assert record.generated_at is None

    def test_unknown_session_renders_empty_context(
        self,
        mini_log: OCELLog,
        render_index: RenderIndex,
        crosswalk: Crosswalk,
        ruleset: RuleSet,
    ) -> None:
        record = render(
            _violation(session_id=""),
            crosswalk=crosswalk,
            ruleset=ruleset,
            render_index=render_index,
            input_log_sha256=log_hash(mini_log),
            crosswalk_sha256=FIXTURE_CROSSWALK_SHA256,
        )
        assert record.context.policy_version == ""
        assert record.context.model_versions == {}

    def test_unknown_rule_raises(
        self,
        mini_log: OCELLog,
        render_index: RenderIndex,
        crosswalk: Crosswalk,
        ruleset: RuleSet,
    ) -> None:
        with pytest.raises(UnknownRuleError, match="T9"):
            render(
                _violation(constraint_id="T9.heldout"),
                crosswalk=crosswalk,
                ruleset=ruleset,
                render_index=render_index,
                input_log_sha256=log_hash(mini_log),
                crosswalk_sha256=FIXTURE_CROSSWALK_SHA256,
            )

    def test_severity_disagreement_raises(
        self,
        mini_log: OCELLog,
        render_index: RenderIndex,
        crosswalk: Crosswalk,
        ruleset: RuleSet,
    ) -> None:
        with pytest.raises(RenderError, match="severity"):
            render(
                _violation(severity=Severity.LOW),
                crosswalk=crosswalk,
                ruleset=ruleset,
                render_index=render_index,
                input_log_sha256=log_hash(mini_log),
                crosswalk_sha256=FIXTURE_CROSSWALK_SHA256,
            )

    def test_unmapped_event_raises(
        self,
        mini_log: OCELLog,
        render_index: RenderIndex,
        crosswalk: Crosswalk,
        ruleset: RuleSet,
    ) -> None:
        with pytest.raises(UnmappedEventError, match="EV-MISSING"):
            render(
                _violation(event_ids=("EV-MISSING",)),
                crosswalk=crosswalk,
                ruleset=ruleset,
                render_index=render_index,
                input_log_sha256=log_hash(mini_log),
                crosswalk_sha256=FIXTURE_CROSSWALK_SHA256,
            )

    def test_cross_trace_citation_raises(
        self,
        mini_log: OCELLog,
        render_index: RenderIndex,
        crosswalk: Crosswalk,
        ruleset: RuleSet,
    ) -> None:
        """Engine violations never span sessions; a citation that does is a
        defect upstream, not something to silently collapse."""
        with pytest.raises(CrossTraceEvidenceError):
            render(
                _violation(event_ids=("EV-A-09", "EV-B-06")),
                crosswalk=crosswalk,
                ruleset=ruleset,
                render_index=render_index,
                input_log_sha256=log_hash(mini_log),
                crosswalk_sha256=FIXTURE_CROSSWALK_SHA256,
            )


class TestRenderAll:
    def test_renders_chains_and_verifies(
        self,
        mini_log: OCELLog,
        render_index: RenderIndex,
        crosswalk: Crosswalk,
        ruleset: RuleSet,
    ) -> None:
        violations = [
            _violation(
                constraint_id="T4.reason_code_presence",
                event_ids=("EV-B-06",),
                object_ids=("DEC-B",),
                session_id="SES-B",
                severity=Severity.MEDIUM,
            ),
            _violation(),
        ]
        records = render_all(
            violations,
            crosswalk=crosswalk,
            ruleset=ruleset,
            render_index=render_index,
            input_log_sha256=log_hash(mini_log),
            crosswalk_sha256=FIXTURE_CROSSWALK_SHA256,
        )
        assert [record.constraint.id for record in records] == [
            "T1.synchronised_approval",  # canonical order, not input order
            "T4.reason_code_presence",
        ]
        assert verify_chain(records)
        assert records[0].integrity is not None
        assert records[0].integrity.prev_record_sha256 == "0" * 64

"""The study-pack markdown renderer (Phase 6, Study A/B materials)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditors_trace.constraints.ruleset import RuleSet, Severity, load_ruleset
from auditors_trace.constraints.templates import Violation
from auditors_trace.evidence.chain import sha256_bytes
from auditors_trace.evidence.crosswalk import Crosswalk, load_crosswalk
from auditors_trace.evidence.pack import session_excerpt, study_pack_markdown
from auditors_trace.evidence.record import EvidenceRecord
from auditors_trace.evidence.renderer import build_render_index, render
from auditors_trace.ingest.mapper import EventSpanRef, SessionSpanRef, SpanIndex
from auditors_trace.model.log import OCELLog, log_hash

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURE_CROSSWALK = REPO / "tests" / "fixtures" / "crosswalk_fixture.yaml"
COMMITTED_PACK = REPO / "docs" / "study" / "evidence-example.md"
GENERATED_OCEL = REPO / "data" / "generated" / "ocel" / "single.jsonocel"


def _synthetic_index(log: OCELLog) -> SpanIndex:
    events = tuple(
        EventSpanRef(
            event_id=event.event_id,
            trace_id=("a" if "-A-" in event.event_id else "b") * 32,
            span_id=f"{position:016x}",
            enrichment_span_ids=(),
        )
        for position, event in enumerate(log.events, start=1)
    )
    sessions = (
        SessionSpanRef(
            session_id="SES-A", trace_id="a" * 32, root_span_id="f" * 16, session_span_ids=()
        ),
        SessionSpanRef(
            session_id="SES-B", trace_id="b" * 32, root_span_id="e" * 16, session_span_ids=()
        ),
    )
    return SpanIndex(schema_version=1, sessions=sessions, events=events)


@pytest.fixture(scope="module")
def example_record(mini_log: OCELLog) -> EvidenceRecord:
    ruleset: RuleSet = load_ruleset(REPO / "rules" / "rules.yaml")
    crosswalk: Crosswalk = load_crosswalk(
        FIXTURE_CROSSWALK, required_ids={rule.constraint_id for rule in ruleset.rules}
    )
    violation = Violation(
        constraint_id="T1.synchronised_approval",
        ocel_event_ids=("EV-A-09",),
        ocel_object_ids=("DEC-A",),
        session_id="SES-A",
        severity=Severity.HIGH,
    )
    return render(
        violation,
        crosswalk=crosswalk,
        ruleset=ruleset,
        render_index=build_render_index(mini_log, _synthetic_index(mini_log)),
        input_log_sha256=log_hash(mini_log),
        crosswalk_sha256=sha256_bytes(FIXTURE_CROSSWALK.read_bytes()),
    )


def test_pack_is_deterministic(example_record: EvidenceRecord, mini_log: OCELLog) -> None:
    outputs = {
        study_pack_markdown(example_record, mini_log, source_label="test") for _ in range(10)
    }
    assert len(outputs) == 1


def test_fenced_json_parses_back_to_the_record(
    example_record: EvidenceRecord, mini_log: OCELLog
) -> None:
    markdown = study_pack_markdown(example_record, mini_log)
    fenced = markdown.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(fenced) == example_record.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


def test_pack_contains_no_placeholders(example_record: EvidenceRecord, mini_log: OCELLog) -> None:
    markdown = study_pack_markdown(example_record, mini_log)
    assert "TODO" not in markdown
    assert "TBD" not in markdown


def test_cited_rows_are_marked_exactly(example_record: EvidenceRecord, mini_log: OCELLog) -> None:
    markdown = study_pack_markdown(example_record, mini_log)
    assert markdown.count("<-- cited") == len(example_record.evidence.ocel_event_ids)
    assert "`EV-A-09`" in markdown


def test_excerpt_includes_the_session_start(mini_log: OCELLog) -> None:
    """Membership is any-relation-to-the-session-object: the session_start
    that only DECLARES the session still belongs to the excerpt."""
    excerpt = session_excerpt(mini_log, "SES-A", ("EV-A-09",))
    assert "EV-A-01" in excerpt
    assert "EV-B-01" not in excerpt
    with pytest.raises(ValueError, match="SES-X"):
        session_excerpt(mini_log, "SES-X", ())


@pytest.mark.skipif(
    not GENERATED_OCEL.exists(), reason="full-scale corpus absent; run make scenario/inject/ingest"
)
def test_committed_study_pack_recomputes(tmp_path: Path) -> None:
    """The committed docs/study/evidence-example.md is exactly what `cli pack`
    regenerates from the live single split and the fixture crosswalk
    (results/e2_coverage.json precedent for committed derived artefacts)."""
    from auditors_trace.cli import main

    out = tmp_path / "evidence-example.md"
    argv = [
        "pack",
        "--ocel",
        str(GENERATED_OCEL),
        "--crosswalk",
        str(FIXTURE_CROSSWALK),
        "--source-label",
        "the `single` evaluation split (seed 42); crosswalk content from the "
        "TEST FIXTURE tests/fixtures/crosswalk_fixture.yaml pending the "
        "human-authored rules/crosswalk.yaml",
        "--out",
        str(out),
    ]
    assert main(argv) == 0
    assert out.read_bytes() == COMMITTED_PACK.read_bytes()

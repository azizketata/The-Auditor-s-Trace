"""The `check` rerun command (Phase 6): exit codes and byte-identical output.

Runs the full check pipeline over the mini fixture written to disk — no
scenario extra needed — with a synthetic span index and the fixture crosswalk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auditors_trace.cli import main
from auditors_trace.constraints.engine import evaluate
from auditors_trace.constraints.ruleset import load_ruleset
from auditors_trace.evidence.chain import records_jsonl
from auditors_trace.evidence.crosswalk import load_crosswalk
from auditors_trace.evidence.renderer import build_render_index, render_all
from auditors_trace.ingest.mapper import EventSpanRef, SessionSpanRef, SpanIndex, span_index_json
from auditors_trace.model.io import write_ocel
from auditors_trace.model.log import OCELLog, log_hash

REPO = Path(__file__).resolve().parent.parent.parent
RULES = REPO / "rules" / "rules.yaml"
FIXTURE_CROSSWALK = REPO / "tests" / "fixtures" / "crosswalk_fixture.yaml"
TEMPLATE_CROSSWALK = REPO / "rules" / "crosswalk.template.yaml"


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
def artifacts(mini_log: OCELLog, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("cli")
    ocel_path = tmp / "mini.jsonocel"
    write_ocel(mini_log, ocel_path, "json")
    index_path = tmp / "mini.jsonocel.span_index.json"
    index_path.write_text(span_index_json(_synthetic_index(mini_log)), encoding="utf-8")
    return {"ocel": ocel_path, "index": index_path, "hash": log_hash(mini_log)}


def _check_argv(artifacts: dict[str, object], **overrides: str) -> list[str]:
    values = {
        "--log": str(artifacts["hash"]),
        "--rules": "2026-08.1",
        "--ocel": str(artifacts["ocel"]),
        "--span-index": str(artifacts["index"]),
        "--rules-file": str(RULES),
        "--crosswalk": str(FIXTURE_CROSSWALK),
    }
    values.update(overrides)
    argv = ["check"]
    for flag, value in values.items():
        argv.extend([flag, value])
    return argv


def _direct_bytes(mini_log: OCELLog) -> bytes:
    ruleset = load_ruleset(RULES)
    crosswalk = load_crosswalk(
        FIXTURE_CROSSWALK, required_ids={rule.constraint_id for rule in ruleset.rules}
    )
    records = render_all(
        evaluate(mini_log, ruleset),
        crosswalk=crosswalk,
        ruleset=ruleset,
        render_index=build_render_index(mini_log, _synthetic_index(mini_log)),
        input_log_sha256=log_hash(mini_log),
    )
    return records_jsonl(records)


def test_check_writes_byte_identical_output(
    artifacts: dict[str, object], mini_log: OCELLog, tmp_path: Path
) -> None:
    out = tmp_path / "evidence.jsonl"
    assert main(_check_argv(artifacts, **{"--out": str(out)})) == 0
    payload = out.read_bytes()
    assert payload == _direct_bytes(mini_log)
    assert payload  # the mini fixture is non-compliant under the shipped rules
    assert b"\r" not in payload


def test_check_stdout_is_raw_bytes(
    artifacts: dict[str, object],
    mini_log: OCELLog,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    assert main(_check_argv(artifacts)) == 0
    captured = capsysbinary.readouterr()
    assert captured.out == _direct_bytes(mini_log)
    assert b"\r" not in captured.out


def test_usage_error_is_exit_2(capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    assert main(["not-a-command"]) == 2
    assert main([]) == 2


def test_missing_input_is_exit_3(artifacts: dict[str, object]) -> None:
    assert main(_check_argv(artifacts, **{"--ocel": "nope/missing.jsonocel"})) == 3
    assert main(_check_argv(artifacts, **{"--crosswalk": "nope/crosswalk.yaml"})) == 3


def test_hash_mismatch_is_exit_4(artifacts: dict[str, object]) -> None:
    assert main(_check_argv(artifacts, **{"--log": "f" * 64})) == 4


def test_ruleset_version_mismatch_is_exit_4(artifacts: dict[str, object]) -> None:
    assert main(_check_argv(artifacts, **{"--rules": "1999-01.0"})) == 4


def test_placeholder_crosswalk_is_exit_6(artifacts: dict[str, object]) -> None:
    """The shipped all-TODO template can never back an evidence run."""
    assert main(_check_argv(artifacts, **{"--crosswalk": str(TEMPLATE_CROSSWALK)})) == 6


def test_malformed_span_index_is_exit_7(artifacts: dict[str, object], tmp_path: Path) -> None:
    bad = tmp_path / "bad.span_index.json"
    bad.write_text('{"schema_version": 2, "sessions": [], "events": []}', encoding="utf-8")
    assert main(_check_argv(artifacts, **{"--span-index": str(bad)})) == 7

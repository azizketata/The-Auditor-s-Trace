"""Phase 3 acceptance: byte-exact golden outputs and the C2 coverage gate.

The committed goldens under tests/golden/mapper/ are the mapper's frozen
output for the frozen span fixtures. If any byte moves, either the mapper
changed (bump the goldens deliberately, with a diff review) or determinism
broke (stop and find out which).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditors_trace.ingest.attribute_map import coverage_json, mapped_fraction
from auditors_trace.ingest.mapper import map_span_trees, span_index_json
from auditors_trace.ingest.otel_reader import build_span_trees, read_spans
from auditors_trace.model.io import read_ocel, write_ocel
from auditors_trace.model.log import log_hash

TESTS = Path(__file__).resolve().parent.parent
SPANS = TESTS / "golden" / "spans"
GOLDEN = TESTS / "golden" / "mapper"
REPO = TESTS.parent

#: Pinned log hashes — asserted identically on Windows and Ubuntu by CI.
PINNED_HASHES = {
    "credit_grant_approval_seed42": (
        "4e3e195c3c857be9079d480a8cd995dbd2b576ac259a64f7f595faf9bd325490"
    ),
    "credit_deny_approval_refer_seed2": (
        "29cf6e5f16b08b24d288213275028c469edab3265318cefd3a69ead4a5285419"
    ),
    "paired_vocabulary": ("de9e865b400cad0e75aba27686bc880f4ba1abff60b297215f59b5d9b847f9a2"),
}

CASES = [
    pytest.param("credit_grant_approval_seed42.jsonl", "credit_grant_approval_seed42", id="grant"),
    pytest.param(
        "credit_deny_approval_refer_seed2.jsonl", "credit_deny_approval_refer_seed2", id="deny"
    ),
    pytest.param("paired_vocabulary_genai.jsonl", "paired_vocabulary", id="paired"),
]


@pytest.mark.parametrize(("spans_name", "golden_name"), CASES)
def test_mapper_golden_small(spans_name: str, golden_name: str, tmp_path: Path) -> None:
    run = map_span_trees(build_span_trees(read_spans(SPANS / spans_name)))

    assert log_hash(run.log) == PINNED_HASHES[golden_name]

    out = tmp_path / f"{golden_name}.ocel.json"
    write_ocel(run.log, out, "json")
    assert out.read_bytes() == (GOLDEN / f"{golden_name}.ocel.json").read_bytes()

    assert (
        coverage_json(run.coverage, None).encode("utf-8")
        == (GOLDEN / f"{golden_name}.coverage.json").read_bytes()
    )
    assert (
        span_index_json(run.span_index).encode("utf-8")
        == (GOLDEN / f"{golden_name}.span_index.json").read_bytes()
    )

    # The committed golden itself round-trips into the identical model.
    assert read_ocel(GOLDEN / f"{golden_name}.ocel.json", "json") == run.log


def test_attribute_coverage_report() -> None:
    """The C2 gate: >0.9 of governance-relevant attributes mapped."""
    for spans_name in (
        "credit_grant_approval_seed42.jsonl",
        "credit_deny_approval_refer_seed2.jsonl",
    ):
        run = map_span_trees(build_span_trees(read_spans(SPANS / spans_name)))
        fraction = mapped_fraction(run.coverage)
        assert fraction > 0.9
        assert fraction == 1.0  # the scripted scenario is fully instrumented
        assert run.coverage.census_total == sum(
            total for _, _, total in run.coverage.per_event_type
        )

    committed = REPO / "results" / "e2_coverage.json"
    payload = json.loads(committed.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["mapped_fraction"] > 0.9
    assert payload["run"]["session_count"] == 50

    manifest = REPO / "data" / "generated" / "spans" / "manifest.json"
    if not manifest.exists():
        pytest.skip("data/generated/spans not present; recompute check runs after make scenario")
    from auditors_trace.ingest.otel_reader import read_manifest, read_run

    run_view = read_manifest(manifest)
    mapped = map_span_trees(build_span_trees(read_run(manifest)))
    assert coverage_json(mapped.coverage, run_view) == committed.read_text(encoding="utf-8")

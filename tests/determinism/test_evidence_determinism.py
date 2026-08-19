"""Phase 6 determinism: identical evidence bytes across runs and read paths.

The forecast in test_placeholder.py — "identical record hashes across runs" —
lands here: 100 renders of one mapped run and 10 fully fresh pipelines all
produce one byte string. CI runs this on Windows and Ubuntu; the pinned golden
in tests/integration/test_evidence.py closes the cross-platform loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("opentelemetry")

from auditors_trace.constraints.engine import evaluate
from auditors_trace.constraints.ruleset import load_ruleset
from auditors_trace.evidence.chain import canonical_json
from auditors_trace.evidence.crosswalk import load_crosswalk
from auditors_trace.evidence.renderer import build_render_index, render_all
from auditors_trace.model.log import log_hash

REPO = Path(__file__).resolve().parent.parent.parent
CATALOGUE = REPO / "data" / "catalogue" / "violations.yaml"
FIXTURE_SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"
FIXTURE_CROSSWALK = REPO / "tests" / "fixtures" / "crosswalk_fixture.yaml"

SIZES = {"clean": 4, "single": 8, "mixed": 4}


@pytest.fixture(scope="module")
def split_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from auditors_trace.scenario.agents import run_fleet
    from auditors_trace.scenario.injector import inject_run, load_catalogue

    base = tmp_path_factory.mktemp("det_base") / "spans"
    run_fleet(16, 7, base, provider="scripted", seed_data=FIXTURE_SAMPLE, quiet=True)
    out = tmp_path_factory.mktemp("det_splits")
    inject_run(base, out, 7, load_catalogue(CATALOGUE), SIZES)
    return out / "single"


def _pipeline_bytes(split_dir: Path) -> bytes:
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_run

    run = map_span_trees(build_span_trees(read_run(split_dir / "manifest.json")))
    ruleset = load_ruleset(REPO / "rules" / "rules.yaml")
    crosswalk = load_crosswalk(
        FIXTURE_CROSSWALK, required_ids={rule.constraint_id for rule in ruleset.rules}
    )
    records = render_all(
        evaluate(run.log, ruleset),
        crosswalk=crosswalk,
        ruleset=ruleset,
        render_index=build_render_index(run.log, run.span_index),
        input_log_sha256=log_hash(run.log),
    )
    return "".join(
        canonical_json(record.model_dump(mode="json", by_alias=True, exclude_none=True)) + "\n"
        for record in records
    ).encode("utf-8")


def test_render_is_deterministic_100x(split_dir: Path) -> None:
    """100 renders over one mapped run: one distinct byte string."""
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_run

    run = map_span_trees(build_span_trees(read_run(split_dir / "manifest.json")))
    ruleset = load_ruleset(REPO / "rules" / "rules.yaml")
    crosswalk = load_crosswalk(
        FIXTURE_CROSSWALK, required_ids={rule.constraint_id for rule in ruleset.rules}
    )
    render_index = build_render_index(run.log, run.span_index)
    violations = evaluate(run.log, ruleset)
    input_hash = log_hash(run.log)
    outputs = {
        "".join(
            canonical_json(record.model_dump(mode="json", by_alias=True, exclude_none=True)) + "\n"
            for record in render_all(
                violations,
                crosswalk=crosswalk,
                ruleset=ruleset,
                render_index=render_index,
                input_log_sha256=input_hash,
            )
        )
        for _ in range(100)
    }
    assert len(outputs) == 1


def test_full_pipeline_is_deterministic_10x(split_dir: Path) -> None:
    """10 fully fresh pipelines (read files -> map -> evaluate -> render):
    one byte string."""
    outputs = {_pipeline_bytes(split_dir) for _ in range(10)}
    assert len(outputs) == 1

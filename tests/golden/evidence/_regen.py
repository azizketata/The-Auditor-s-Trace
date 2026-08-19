"""Cut the golden evidence bundle: hermetic_single.evidence.jsonl.

Run deliberately, never casually:

    uv run python tests/golden/evidence/_regen.py

Re-cut ONLY when one of these changes, and commit the diff after review:
- tests/fixtures/crosswalk_fixture.yaml (records embed its text verbatim)
- rules/rules.yaml (formal statements, legal bases, ruleset_version)
- engine_version() (any behavioural change to the engine/templates)
- when the human-authored rules/crosswalk.yaml replaces the fixture
  (pre-freeze; re-cut against it in a dedicated commit)

The pipeline mirrors tests/integration/test_evidence.py exactly: a scripted
16-session run (seed 7), splits 4/8/4, the single split mapped and rendered
with the fixture crosswalk. Requires the scenario extra (opentelemetry).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
GOLDEN = Path(__file__).resolve().parent / "hermetic_single.evidence.jsonl"


def main() -> None:
    from auditors_trace.constraints.engine import evaluate
    from auditors_trace.constraints.ruleset import load_ruleset
    from auditors_trace.evidence.chain import records_jsonl
    from auditors_trace.evidence.crosswalk import load_crosswalk
    from auditors_trace.evidence.renderer import build_render_index, render_all
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_run
    from auditors_trace.model.log import log_hash
    from auditors_trace.scenario.agents import run_fleet
    from auditors_trace.scenario.injector import inject_run, load_catalogue

    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch) / "spans"
        run_fleet(
            16,
            7,
            base,
            provider="scripted",
            seed_data=REPO / "tests" / "fixtures" / "german_credit_sample.data",
            quiet=True,
        )
        splits = Path(scratch) / "splits"
        inject_run(
            base,
            splits,
            7,
            load_catalogue(REPO / "data" / "catalogue" / "violations.yaml"),
            {"clean": 4, "single": 8, "mixed": 4},
        )
        run = map_span_trees(build_span_trees(read_run(splits / "single" / "manifest.json")))

    ruleset = load_ruleset(REPO / "rules" / "rules.yaml")
    crosswalk = load_crosswalk(
        REPO / "tests" / "fixtures" / "crosswalk_fixture.yaml",
        required_ids={rule.constraint_id for rule in ruleset.rules},
    )
    records = render_all(
        evaluate(run.log, ruleset),
        crosswalk=crosswalk,
        ruleset=ruleset,
        render_index=build_render_index(run.log, run.span_index),
        input_log_sha256=log_hash(run.log),
    )
    GOLDEN.write_bytes(records_jsonl(records))
    print(f"{len(records)} records -> {GOLDEN}")


if __name__ == "__main__":
    main()

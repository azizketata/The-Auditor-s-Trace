"""Dev-split hygiene and the live judge smoke (Phase 7).

The dev split (seed 4207 over the 50-session base run) is the ONLY data
judge prompts may be developed on; its disjointness from the evaluation base
run is pinned here. The live smoke runs one real call and is network-marked —
CI never executes it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from auditors_trace.baselines.llm_judge import EVALUATION_BASE_RUN_ID

REPO = Path(__file__).resolve().parent.parent.parent
DEV = REPO / "data" / "generated" / "dev"
SPANS_50 = REPO / "data" / "generated" / "spans"

dev_data = pytest.mark.skipif(
    not (DEV / "single" / "manifest.json").exists(),
    reason="dev split absent; run `scenario inject --spans data/generated/spans "
    "--seed 4207 --out data/generated/dev --sizes clean=10,single=32,mixed=8`",
)


@dev_data
def test_dev_split_is_disjoint_from_the_evaluation_base() -> None:
    base_manifest = json.loads((SPANS_50 / "manifest.json").read_text(encoding="utf-8"))
    assert base_manifest["run_id"] == "e1f8535c07681d95"
    assert base_manifest["run_id"] != EVALUATION_BASE_RUN_ID
    for split in ("clean", "single", "mixed"):
        manifest = json.loads((DEV / split / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_id"] == base_manifest["run_id"], split


@dev_data
@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="live smoke needs ANTHROPIC_API_KEY"
)
def test_live_judge_smoke_one_session() -> None:
    """One real haiku call on one dev session: parses or reports a schema
    failure, and lands in the committed cache."""
    from auditors_trace.baselines.llm_judge import judge_all
    from auditors_trace.model.io import detect_serialisation, read_ocel

    ocel_path = DEV / "ocel" / "single.jsonocel"
    log = read_ocel(ocel_path, detect_serialisation(ocel_path))
    session = sorted(obj.object_id for obj in log.objects if obj.object_type.value == "Session")[0]
    runs = judge_all(
        DEV / "single",
        log,
        models=("claude-haiku-4-5",),
        samples=1,
        conditions=("ocel",),
        provider="anthropic",
    )
    run = next(r for r in runs if r.session_id == session)
    assert run.schema_failure or run.violations is not None

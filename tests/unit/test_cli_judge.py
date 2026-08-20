"""The `judge` CLI subcommand (Phase 7): exit codes and the scripted path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditors_trace.baselines.llm_judge import EVALUATION_BASE_RUN_ID
from auditors_trace.cli import main
from auditors_trace.model.io import write_ocel
from auditors_trace.model.log import OCELLog

REPO = Path(__file__).resolve().parent.parent.parent
RULES = REPO / "rules" / "rules.yaml"
PROMPTS = REPO / "rules" / "judge_prompts.yaml"


@pytest.fixture(scope="module")
def dev_split(mini_log: OCELLog, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("judge_cli")
    split = tmp / "split"
    split.mkdir()
    (split / "manifest.json").write_text(
        json.dumps({"run_id": "e1f8535c07681d95"}), encoding="utf-8"
    )
    ocel_path = tmp / "mini.jsonocel"
    write_ocel(mini_log, ocel_path, "json")
    return {"split": split, "ocel": ocel_path, "tmp": tmp}


def _argv(dev_split: dict[str, Path], cache: Path, **overrides: str) -> list[str]:
    values = {
        "--split-dir": str(dev_split["split"]),
        "--ocel": str(dev_split["ocel"]),
        "--model": "claude-haiku-4-5",
        "--samples": "1",
        "--condition": "ocel",
        "--cache-dir": str(cache),
        "--provider": "scripted",
        "--prompts": str(PROMPTS),
        "--rules-file": str(RULES),
    }
    values.update(overrides)
    argv = ["judge"]
    for flag, value in values.items():
        argv.extend([flag, value])
    return argv


def test_scripted_end_to_end_writes_the_cache(dev_split: dict[str, Path], tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    assert main(_argv(dev_split, cache)) == 0
    cached = list(cache.glob("*.json"))
    assert len(cached) == 2  # SES-A + SES-B, one model, one sample, one condition
    record = json.loads(cached[0].read_text(encoding="utf-8"))
    assert record["fingerprint"]["sampling"] == "provider_default"

    # Second invocation replays entirely from cache (no new files, exit 0).
    before = {path.name: path.read_bytes() for path in cached}
    assert main(_argv(dev_split, cache)) == 0
    after = {path.name: path.read_bytes() for path in cache.glob("*.json")}
    assert after == before


def test_usage_error_is_exit_2() -> None:
    assert main(["judge", "--condition", "sideways"]) == 2


def test_missing_split_is_exit_3(dev_split: dict[str, Path], tmp_path: Path) -> None:
    argv = _argv(dev_split, tmp_path, **{"--split-dir": str(tmp_path / "nope")})
    assert main(argv) == 3


def test_provider_unavailable_is_exit_3(
    dev_split: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    argv = _argv(dev_split, tmp_path, **{"--provider": "anthropic"})
    assert main(argv) == 3


def test_evaluation_lock_is_exit_4(
    dev_split: dict[str, Path], mini_log: OCELLog, tmp_path: Path
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "manifest.json").write_text(
        json.dumps({"run_id": EVALUATION_BASE_RUN_ID}), encoding="utf-8"
    )
    argv = _argv(dev_split, tmp_path / "cache", **{"--split-dir": str(locked)})
    assert main(argv) == 4

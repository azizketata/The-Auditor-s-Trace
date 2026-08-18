"""Two identical scripted runs produce identical governance telemetry.

Exactly two kinds of field are volatile by design, and this test names both:

- span ``start/end_time_unix_nano`` (and span-event timestamps) are *real*
  telemetry — determinism is required of the analysis pipeline, which orders
  by ``at.event.seq`` and timestamps from the simulated clock, never wall
  time;
- ``langgraph_checkpoint_ns`` / ``checkpoint_ns`` inside the OpenInference
  ``metadata`` JSON blob on layer-B spans — LangGraph generates a task UUID
  per run and its contextvar metadata merges into every child invocation.

Everything else — ids, attributes, object declarations, on-disk order — must
be byte-identical. Layer-A spans (the governance layer) carry neither volatile
field except the wall-clock nanos.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from auditors_trace.scenario.agents import run_fleet

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    dir_a = tmp_path_factory.mktemp("run_a")
    dir_b = tmp_path_factory.mktemp("run_b")
    run_fleet(2, 42, dir_a, seed_data=FIXTURE, quiet=True)
    run_fleet(2, 42, dir_b, seed_data=FIXTURE, quiet=True)
    return dir_a, dir_b


def _manifest(path: Path) -> dict[str, Any]:
    payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_scenario_output_is_identical_across_runs(
    two_runs: tuple[Path, Path], span_line_normaliser: Callable[[str], str]
) -> None:
    dir_a, dir_b = two_runs
    files_a = [name for name, _ in _manifest(dir_a)["files"]]
    files_b = [name for name, _ in _manifest(dir_b)["files"]]
    assert files_a == files_b
    for name in files_a:
        lines_a = (dir_a / name).read_text(encoding="utf-8").splitlines()
        lines_b = (dir_b / name).read_text(encoding="utf-8").splitlines()
        assert len(lines_a) == len(lines_b), name
        for i, (a, b) in enumerate(zip(lines_a, lines_b, strict=True)):
            assert span_line_normaliser(a) == span_line_normaliser(b), f"{name} line {i}"


def test_layer_a_spans_have_no_volatile_content(two_runs: tuple[Path, Path]) -> None:
    # The governance layer itself must not carry the LangGraph UUID blob.
    dir_a, _ = two_runs
    for name, _digest in _manifest(dir_a)["files"]:
        for line in (dir_a / name).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            attrs = row["attributes"]
            if any(key.startswith("at.") for key in attrs):
                assert "metadata" not in attrs, row["name"]


def test_manifest_is_stable_except_file_digests(two_runs: tuple[Path, Path]) -> None:
    dir_a, dir_b = two_runs
    manifest_a, manifest_b = _manifest(dir_a), _manifest(dir_b)
    # File digests cover wall-clock nanoseconds, so they legitimately differ.
    manifest_a.pop("files")
    manifest_b.pop("files")
    assert manifest_a == manifest_b


def test_trace_and_span_ids_are_content_derived(two_runs: tuple[Path, Path]) -> None:
    dir_a, dir_b = two_runs
    for name, _digest in _manifest(dir_a)["files"]:
        ids_a = [
            (json.loads(line)["trace_id"], json.loads(line)["span_id"])
            for line in (dir_a / name).read_text(encoding="utf-8").splitlines()
        ]
        ids_b = [
            (json.loads(line)["trace_id"], json.loads(line)["span_id"])
            for line in (dir_b / name).read_text(encoding="utf-8").splitlines()
        ]
        assert ids_a == ids_b, name

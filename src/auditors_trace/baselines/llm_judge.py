"""LLM-as-judge baseline.

Phase 7 deliverable. The second and last place an LLM may appear (invariant I2).

Three models by five seeds. Every response is cached to disk keyed by
(model, seed, input hash) so the evaluation replays without API access.

Output is parsed against a fixed schema and otherwise left alone: parse failures
count as errors and are reported, never cleaned up or normalised away.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auditors_trace.constraints.templates import Violation
from auditors_trace.model.log import OCELLog


@dataclass(frozen=True, slots=True)
class JudgeRun:
    """One (model, seed) pass: its parsed violations and any parse errors."""


def judge(log: OCELLog, model: str, seed: int, cache_dir: Path) -> JudgeRun:
    """Run one judging pass, reading from cache when the input hash matches."""
    raise NotImplementedError


def judge_all(log: OCELLog, models: list[str], seeds: list[int], cache_dir: Path) -> list[JudgeRun]:
    """Run every (model, seed) combination."""
    raise NotImplementedError


def modal_verdict(runs: list[JudgeRun]) -> list[Violation]:
    """Return the most frequently produced violation set across runs."""
    raise NotImplementedError

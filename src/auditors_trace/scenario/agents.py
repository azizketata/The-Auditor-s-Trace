"""The four-agent credit workflow plus a simulated human approval step.

Phase 2 deliverable. Agents: intake, data_retrieval, scoring, adjudication.
Instrumented with OpenInference; raw spans land in ``data/generated/spans/``.

Temperature 0 and a fixed seed on every LLM call, both recorded. LLM *output*
is still not bitwise reproducible, and that is expected: determinism is required
of the analysis pipeline, not of the subject under test.

The agent graph stays declarative so a second scenario can be swapped in without
touching the mapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auditors_trace.scenario.seed import Applicant


@dataclass(frozen=True, slots=True)
class FleetConfig:
    """Declarative agent-graph definition. Fields land in Phase 2."""


def default_fleet() -> FleetConfig:
    """Return the credit-scoring fleet: intake, retrieval, scoring, adjudication."""
    raise NotImplementedError


def run_session(applicant: Applicant, fleet: FleetConfig, seed: int) -> None:
    """Run one application end to end, emitting spans through the OTel exporter."""
    raise NotImplementedError


def run_fleet(count: int, seed: int, out_dir: Path, fleet: FleetConfig | None = None) -> Path:
    """Run ``count`` sessions and write the raw spans. Returns the span directory."""
    raise NotImplementedError

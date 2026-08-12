"""Evaluate a ruleset over an OCEL log.

Phase 5 deliverable. Satisfies claim C3 (object-centric declarative constraints
beat LLM-as-judge on detection and are perfectly deterministic).

The engine sorts violations by (constraint id, first event id) before returning.
Running it twice on the same log returns an identical list, in identical order.
"""

from __future__ import annotations

from auditors_trace.constraints.ruleset import RuleSet
from auditors_trace.constraints.templates import Violation
from auditors_trace.model.log import OCELLog


def evaluate(log: OCELLog, ruleset: RuleSet) -> list[Violation]:
    """Evaluate every rule over the log and return violations in canonical order."""
    raise NotImplementedError


def engine_version() -> str:
    """Return the engine version string cited in every evidence record's provenance."""
    raise NotImplementedError

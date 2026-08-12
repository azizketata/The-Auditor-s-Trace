"""Load and validate `rules/rules.yaml`.

Phase 5 deliverable. Invariant I3 is enforced here: a rule without a legal
article reference is a load-time error, by design. Do not relax this.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class Severity(StrEnum):
    """Violation severity, as carried into the evidence record."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LegalReference(BaseModel):
    """A citation into a legal instrument. Required on every rule (invariant I3)."""


class Rule(BaseModel):
    """One instantiated constraint template with its parameters and legal basis."""


class RuleSet(BaseModel):
    """A versioned collection of rules."""


def load_ruleset(path: Path) -> RuleSet:
    """Load and validate a ruleset. Raises if any rule lacks an article reference."""
    raise NotImplementedError


def ruleset_version(ruleset: RuleSet) -> str:
    """Return the version string cited by every evidence record this ruleset produces."""
    raise NotImplementedError

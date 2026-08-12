"""Object-scoped analogues of classic DECLARE.

Phase 5 deliverable. Needed for the E1 expressiveness comparison against
case-centric DECLARE: these are the constraints that survive object-centricity
and whose flattened counterparts do not.
"""

from __future__ import annotations

from auditors_trace.constraints.ruleset import Rule
from auditors_trace.constraints.templates import Violation
from auditors_trace.model.log import OCELLog


def object_existence(log: OCELLog, rule: Rule) -> list[Violation]:
    """An object of the given type must exist for each anchor object."""
    raise NotImplementedError


def object_absence(log: OCELLog, rule: Rule) -> list[Violation]:
    """No object of the given type may relate to the anchor object."""
    raise NotImplementedError


def synchronised_response(log: OCELLog, rule: Rule) -> list[Violation]:
    """Activity A on a shared object must be followed by activity B on that object."""
    raise NotImplementedError


def synchronised_precedence(log: OCELLog, rule: Rule) -> list[Violation]:
    """Activity B on a shared object must be preceded by activity A on that object."""
    raise NotImplementedError

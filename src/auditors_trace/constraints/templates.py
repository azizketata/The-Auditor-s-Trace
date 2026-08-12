"""The five agent-specific constraint templates.

Phase 5 deliverable, built in the order T1, T4, T5, T2, T3 (section 9). Every
template is a pure function ``(log, rule) -> list[Violation]``. All iteration is
sorted. No template may reference wall-clock time.

Specified in BUILD-PLAN.md section 6; conceptual origin is OC-DECLARE (Küsters
and van der Aalst, BPM 2025), instantiated here for agent fleets.
"""

from __future__ import annotations

from dataclasses import dataclass

from auditors_trace.constraints.ruleset import Rule
from auditors_trace.model.log import OCELLog


@dataclass(frozen=True, slots=True)
class Violation:
    """A constraint breach, carrying the exact event and object ids involved.

    Fields land in Phase 5. Everything the evidence renderer needs to cite must
    be present here; the renderer never re-queries the log.
    """


def t1_synchronised_approval(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every decision requiring approval has a prior grant by an authorised role."""
    raise NotImplementedError


def t2_mandatory_data_coverage(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every decision's application has retrievals covering all required sources."""
    raise NotImplementedError


def t3_delegation_integrity(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every agent action is preceded by a handoff, and no handoff chain cycles."""
    raise NotImplementedError


def t4_reason_code_presence(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every adverse decision carries reason codes and a referencing reasoning event."""
    raise NotImplementedError


def t5_prohibited_attribute_access(log: OCELLog, rule: Rule) -> list[Violation]:
    """No special-category resource is read without a recorded lawful basis."""
    raise NotImplementedError

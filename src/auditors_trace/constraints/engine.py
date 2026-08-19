"""Evaluate a ruleset over an OCEL log.

Phase 5 deliverable. Satisfies claim C3 (object-centric declarative constraints
beat LLM-as-judge on detection and are perfectly deterministic).

The engine sorts violations by (constraint id, first event id) before returning.
Running it twice on the same log returns an identical list, in identical order.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from auditors_trace.constraints import standard, templates
from auditors_trace.constraints.ruleset import Rule, RuleSet
from auditors_trace.constraints.templates import Violation, violation_sort_key
from auditors_trace.model.log import OCELLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    Template = Callable[[OCELLog, Rule], list[Violation]]


class EngineError(ValueError):
    """A rule references a template the engine does not know."""


#: Template name -> template function. Pinned 1:1 to ``ruleset.KNOWN_TEMPLATES``
#: by test, so the loader's vocabulary and the engine's dispatch cannot drift.
#: A held-out template (section 7a) lands as one entry here plus one name
#: there — nothing else moves.
REGISTRY: Final[Mapping[str, Template]] = MappingProxyType(
    {
        "t1_synchronised_approval": templates.t1_synchronised_approval,
        "t2_mandatory_data_coverage": templates.t2_mandatory_data_coverage,
        "t3_delegation_integrity": templates.t3_delegation_integrity,
        "t4_reason_code_presence": templates.t4_reason_code_presence,
        "t5_prohibited_attribute_access": templates.t5_prohibited_attribute_access,
        "object_existence": standard.object_existence,
        "object_absence": standard.object_absence,
        "synchronised_response": standard.synchronised_response,
        "synchronised_precedence": standard.synchronised_precedence,
    }
)


def evaluate(log: OCELLog, ruleset: RuleSet) -> list[Violation]:
    """Evaluate every rule over the log and return violations in canonical order.

    Rules are dispatched in constraint-id order (each template is pure, so
    dispatch order cannot change results — only error-surfacing order), and
    the concatenated result is sorted by ``violation_sort_key``.
    """
    violations: list[Violation] = []
    for rule in sorted(ruleset.rules, key=lambda r: r.constraint_id):
        template = REGISTRY.get(rule.template)
        if template is None:
            raise EngineError(
                f"rule {rule.constraint_id} names unregistered template {rule.template!r}"
            )
        violations.extend(template(log, rule))
    return sorted(violations, key=violation_sort_key)


def engine_version() -> str:
    """Return the engine version string cited in every evidence record's provenance.

    Bump policy: ANY behavioural change to ``templates.py``, ``standard.py``,
    or this module bumps the version — two records citing the same engine
    version must be outputs of the same detection semantics.
    """
    return "engine/0.1.0"

"""Engine dispatch, registry pinning, and canonical output order (Phase 5).

The integration-level acceptance tests (determinism 100x, recall on the
injected splits) live in tests/integration/test_engine.py; this file covers
the engine's own mechanics on the shared mini fixture.
"""

from __future__ import annotations

import pytest

from auditors_trace.constraints.engine import REGISTRY, EngineError, engine_version, evaluate
from auditors_trace.constraints.ruleset import (
    KNOWN_TEMPLATES,
    LegalReference,
    Rule,
    RuleSet,
    Severity,
)
from auditors_trace.model.log import OCELLog


def _rule(constraint_id: str, template: str, severity: Severity, **params: object) -> Rule:
    return Rule(
        constraint_id=constraint_id,
        template=template,
        description="test rule",
        formal="test formal statement",
        severity=severity,
        params=dict(params),
        legal_basis=(
            LegalReference(
                instrument="Regulation (EU) 2024/1689",
                article="14",
                requirement="test requirement",
            ),
        ),
    )


def _ruleset(rules: tuple[Rule, ...]) -> RuleSet:
    return RuleSet(schema_version=1, ruleset_version="2026-08.test", rules=rules)


def _mini_fitted_rules() -> tuple[Rule, ...]:
    """The six shipped constraints with parameters fitted to the mini fixture's
    own vocabulary (its approver role, single source, session A's missing
    handoff). One divergence survives even fitted parameters: session B never
    retrieves data at all, so T2 fires on its decision — pinned below."""
    return (
        _rule(
            "T1.synchronised_approval",
            "t1_synchronised_approval",
            Severity.HIGH,
            allowed_roles=["senior_credit_officer"],
            decision_outcomes=["grant", "deny"],
        ),
        _rule(
            "T2.mandatory_data_coverage",
            "t2_mandatory_data_coverage",
            Severity.MEDIUM,
            required_sources=["credit_bureau"],
        ),
        _rule(
            "T3.delegation_integrity",
            "t3_delegation_integrity",
            Severity.MEDIUM,
            entrypoint_roles=["intake", "adjudication"],
        ),
        _rule(
            "T4.reason_code_presence",
            "t4_reason_code_presence",
            Severity.MEDIUM,
            adverse_outcomes=["deny"],
        ),
        _rule(
            "T5.prohibited_attribute_access",
            "t5_prohibited_attribute_access",
            Severity.HIGH,
            prohibited_classifications=["special_category"],
        ),
        _rule(
            "STD.policy_version_current",
            "object_absence",
            Severity.MEDIUM,
            anchor_event_type="make_decision",
            qualifier="governed_by",
            object_type="PolicyVersion",
            attribute="effective_to",
            violating_when="non_empty",
        ),
    )


def test_registry_is_pinned_to_known_templates() -> None:
    """ruleset.KNOWN_TEMPLATES (names) and engine.REGISTRY (functions) cannot
    drift: they are the same closed vocabulary."""
    assert frozenset(REGISTRY) == KNOWN_TEMPLATES


def test_engine_version_is_a_literal() -> None:
    assert engine_version() == "engine/0.1.0"


def test_mini_log_under_fitted_parameters_has_one_known_divergence(mini_log: OCELLog) -> None:
    violations = evaluate(mini_log, _ruleset(_mini_fitted_rules()))
    assert [(v.constraint_id, v.ocel_event_ids, v.session_id) for v in violations] == [
        ("T2.mandatory_data_coverage", ("EV-B-06",), "SES-B")
    ]


def test_violations_concatenate_across_rules_in_canonical_order(mini_log: OCELLog) -> None:
    """T3 with only `intake` as entrypoint flags session A's adjudication
    action; T4 with refer in scope flags DEC-B's missing reasoning. The engine
    returns both, ordered by (constraint id, event ids)."""
    rules = (
        _rule(
            "T4.reason_code_presence",
            "t4_reason_code_presence",
            Severity.MEDIUM,
            adverse_outcomes=["deny", "refer"],
        ),
        _rule(
            "T3.delegation_integrity",
            "t3_delegation_integrity",
            Severity.MEDIUM,
            entrypoint_roles=["intake"],
        ),
    )
    violations = evaluate(mini_log, _ruleset(rules))
    assert [(v.constraint_id, v.ocel_event_ids) for v in violations] == [
        ("T3.delegation_integrity", ("EV-A-09",)),
        ("T4.reason_code_presence", ("EV-B-06",)),
    ]
    assert all(v.severity is Severity.MEDIUM for v in violations)


def test_engine_error_on_unregistered_template(mini_log: OCELLog) -> None:
    """Defence in depth behind the loader's template check: a rule that
    somehow bypassed validation still fails loudly, never silently."""
    rogue = Rule.model_construct(
        constraint_id="T9.rogue",
        template="not_a_template",
        description="bypassed validation",
        severity=Severity.LOW,
        params={},
        legal_basis=(),
    )
    ruleset = RuleSet.model_construct(
        schema_version=1, ruleset_version="2026-08.test", rules=(rogue,)
    )
    with pytest.raises(EngineError, match="not_a_template"):
        evaluate(mini_log, ruleset)

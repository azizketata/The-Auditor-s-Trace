"""Ruleset schema and loader validation (Phase 5).

Invariant I3 is the point of this file: a rule without a substantive legal
article reference is a load-time error, by design (CLAUDE.md hard rule 4).
Do not relax these tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auditors_trace.constraints.ruleset import (
    KNOWN_TEMPLATES,
    RuleSet,
    load_ruleset,
    ruleset_version,
)

#: The closed constraint-id vocabulary of the six shipped audit rules.
SHIPPED_CONSTRAINT_IDS = frozenset(
    {
        "T1.synchronised_approval",
        "T2.mandatory_data_coverage",
        "T3.delegation_integrity",
        "T4.reason_code_presence",
        "T5.prohibited_attribute_access",
        "STD.policy_version_current",
    }
)


def _legal_basis() -> list[dict[str, str]]:
    return [
        {
            "instrument": "Regulation (EU) 2024/1689",
            "article": "14",
            "paragraph": "4(d)",
            "requirement": "Human oversight over high-risk system output.",
        }
    ]


def _rule(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "constraint_id": "T1.synchronised_approval",
        "template": "t1_synchronised_approval",
        "description": "Every approval-requiring decision has a prior authorised grant.",
        "severity": "high",
        "params": {
            "allowed_roles": ["credit_officer"],
            "decision_outcomes": ["grant", "deny"],
        },
        "legal_basis": _legal_basis(),
    }
    base.update(overrides)
    return base


def _ruleset(rules: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "ruleset_version": "2026-08.test",
        "rules": rules,
    }
    base.update(overrides)
    return base


class TestInvariantI3:
    def test_ruleset_without_article_fails_validation(self) -> None:
        """The mandated acceptance test: no legal basis, no load. Ever."""
        rule_missing = _rule()
        del rule_missing["legal_basis"]
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([rule_missing]))

        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule(legal_basis=[])]))

        vague = _legal_basis()
        vague[0]["article"] = "TBD"
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule(legal_basis=vague)]))

    def test_requirement_text_must_be_substantive(self) -> None:
        for placeholder in ("TODO fill in", "tbd", "  TODO  "):
            basis = _legal_basis()
            basis[0]["requirement"] = placeholder
            with pytest.raises(ValidationError):
                RuleSet.model_validate(_ruleset([_rule(legal_basis=basis)]))

    def test_instrument_must_be_non_empty(self) -> None:
        basis = _legal_basis()
        basis[0]["instrument"] = ""
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule(legal_basis=basis)]))


class TestRuleSchema:
    def test_unknown_template_rejected(self) -> None:
        with pytest.raises(ValidationError, match="template"):
            RuleSet.model_validate(_ruleset([_rule(template="t9_totally_new")]))

    def test_constraint_id_pattern_enforced(self) -> None:
        for bad in ("t1.synchronised_approval", "T1", "T1.Synchronised", ""):
            with pytest.raises(ValidationError):
                RuleSet.model_validate(_ruleset([_rule(constraint_id=bad)]))

    def test_malformed_params_rejected_per_template(self) -> None:
        cases: list[tuple[str, dict[str, Any]]] = [
            ("t1_synchronised_approval", {"allowed_roles": []}),
            ("t1_synchronised_approval", {"decision_outcomes": ["grant"]}),
            ("t2_mandatory_data_coverage", {}),
            ("t3_delegation_integrity", {"entrypoint_roles": []}),
            ("t4_reason_code_presence", {"adverse": ["deny"]}),
            ("t5_prohibited_attribute_access", {"prohibited_classifications": 3}),
            (
                "object_absence",
                {
                    "anchor_event_type": "make_decision",
                    "qualifier": "governed_by",
                    "object_type": "PolicyVersion",
                    # non_empty needs an attribute to inspect
                    "violating_when": "non_empty",
                },
            ),
            (
                "object_absence",
                {
                    "anchor_event_type": "not_an_event",
                    "qualifier": "governed_by",
                    "object_type": "PolicyVersion",
                    "violating_when": "always",
                },
            ),
            (
                "object_absence",
                {
                    "anchor_event_type": "make_decision",
                    "qualifier": "declares",  # declarations are never semantic
                    "object_type": "PolicyVersion",
                    "violating_when": "always",
                },
            ),
        ]
        for template, params in cases:
            constraint_id = "T9.case" if template.startswith("t") else "STD.case"
            with pytest.raises(ValidationError):
                RuleSet.model_validate(
                    _ruleset([_rule(constraint_id=constraint_id, template=template, params=params)])
                )

    def test_extra_keys_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule(surprise=1)]))
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule()], surprise=1))

    def test_unknown_severity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule(severity="critical")]))


class TestRuleSetSchema:
    def test_duplicate_constraint_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            RuleSet.model_validate(_ruleset([_rule(), _rule()]))

    def test_schema_version_pinned(self) -> None:
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule()], schema_version=2))

    def test_empty_rules_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([]))

    def test_empty_ruleset_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule()], ruleset_version=""))


class TestLoader:
    def test_shipped_ruleset_loads(self, repo_root: Path) -> None:
        ruleset = load_ruleset(repo_root / "rules" / "rules.yaml")
        assert {rule.constraint_id for rule in ruleset.rules} == SHIPPED_CONSTRAINT_IDS
        assert ruleset_version(ruleset) == ruleset.ruleset_version
        assert ruleset_version(ruleset)

    def test_missing_file_raises_with_filename(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_ruleset(tmp_path / "nope.yaml")

    def test_non_mapping_yaml_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "rules.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError, match=r"rules\.yaml"):
            load_ruleset(path)

    def test_known_templates_is_the_closed_registry_vocabulary(self) -> None:
        assert KNOWN_TEMPLATES == frozenset(
            {
                "t1_synchronised_approval",
                "t2_mandatory_data_coverage",
                "t3_delegation_integrity",
                "t4_reason_code_presence",
                "t5_prohibited_attribute_access",
                "object_existence",
                "object_absence",
                "synchronised_response",
                "synchronised_precedence",
            }
        )

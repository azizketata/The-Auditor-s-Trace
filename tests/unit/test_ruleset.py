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
        "formal": "forall d in make_decision with outcome in {grant, deny}: exists prior approval",
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
        """Adversarial-review regression (19 Aug 2026): 'tbd' was rejected
        only as an exact match, so 'TBD pending legal review' loaded into
        evidence records. Both markers are substring checks now."""
        placeholders = (
            "TODO fill in",
            "tbd",
            "  TODO  ",
            "TBD pending legal review",
            "Article TBD",
            "tbd - fill later",
        )
        for placeholder in placeholders:
            basis = _legal_basis()
            basis[0]["requirement"] = placeholder
            with pytest.raises(ValidationError):
                RuleSet.model_validate(_ruleset([_rule(legal_basis=basis)]))

    def test_instrument_must_be_non_empty(self) -> None:
        basis = _legal_basis()
        basis[0]["instrument"] = ""
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([_rule(legal_basis=basis)]))

    def test_article_must_be_an_ascii_article_number(self) -> None:
        """Adversarial-review regression: the rust-regex \\d matches any
        Unicode decimal digit, so Arabic-Indic and fullwidth lookalikes
        loaded, as did the nonexistent article \"0\". Articles are ASCII,
        nonzero-leading, or they do not load."""
        fullwidth_14 = chr(0xFF11) + chr(0xFF14)
        arabic_indic_14 = chr(0x0661) + chr(0x0664)
        for bad in (arabic_indic_14, fullwidth_14, "0", "014", ""):
            basis = _legal_basis()
            basis[0]["article"] = bad
            with pytest.raises(ValidationError):
                RuleSet.model_validate(_ruleset([_rule(legal_basis=basis)]))
        good = _legal_basis()
        good[0]["article"] = "16a"
        RuleSet.model_validate(_ruleset([_rule(legal_basis=good)]))

    def test_paragraph_placeholders_rejected(self) -> None:
        """Adversarial-review regression: paragraph carried neither the
        pattern nor the placeholder check, so paragraph: \"TODO\" loaded —
        and the crosswalk template ships literal TODO paragraphs, one
        copy-paste away from audit evidence."""
        for bad in ("TODO", "tbd", "TODO e.g. 4(d)"):
            basis = _legal_basis()
            basis[0]["paragraph"] = bad
            with pytest.raises(ValidationError):
                RuleSet.model_validate(_ruleset([_rule(legal_basis=basis)]))
        for good_value in ("", "2(f)-(g)", "4(d)"):
            basis = _legal_basis()
            basis[0]["paragraph"] = good_value
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
            (
                # equals with no value would default to "" and match every
                # EMPTY attribute — inverting an STD-style predicate.
                "object_absence",
                {
                    "anchor_event_type": "make_decision",
                    "qualifier": "governed_by",
                    "object_type": "PolicyVersion",
                    "attribute": "effective_to",
                    "violating_when": "equals",
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

    def test_formal_statement_required_and_substantive(self) -> None:
        """Phase 6: `formal` feeds evidence records' constraint.formal (§8),
        pre-registered in the ruleset like every other frozen parameter."""
        missing = _rule()
        del missing["formal"]
        with pytest.raises(ValidationError):
            RuleSet.model_validate(_ruleset([missing]))
        for bad in ("", "TODO write the formal statement", "tbd"):
            with pytest.raises(ValidationError):
                RuleSet.model_validate(_ruleset([_rule(formal=bad)]))


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

    def test_duplicate_yaml_keys_rejected(self, tmp_path: Path) -> None:
        """Adversarial-review regression: yaml.safe_load is last-wins on
        duplicate mapping keys, so a merge artifact duplicating a rule's
        legal_basis: (or a whole rules: block) silently discarded the first
        occurrence — validated citations vanishing without any error."""
        import yaml

        base = yaml.safe_load(
            (Path(__file__).resolve().parent.parent.parent / "rules" / "rules.yaml").read_text(
                encoding="utf-8"
            )
        )
        rule = base["rules"][0]
        duplicated_key = (
            "schema_version: 1\n"
            'ruleset_version: "2026-08.test"\n'
            "rules:\n"
            "  - constraint_id: T1.synchronised_approval\n"
            "    template: t1_synchronised_approval\n"
            "    description: duplicate-key probe\n"
            "    severity: high\n"
            "    severity: low\n"
            "    params:\n"
            "      allowed_roles: [credit_officer]\n"
            "      decision_outcomes: [grant, deny]\n"
            "    legal_basis:\n"
            '      - instrument: "Regulation (EU) 2024/1689"\n'
            '        article: "14"\n'
            '        requirement: "Human oversight over the output."\n'
        )
        path = tmp_path / "rules.yaml"
        path.write_text(duplicated_key, encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate"):
            load_ruleset(path)

        assert rule["constraint_id"]  # the shipped file itself parses cleanly

        shadowed_block = 'schema_version: 1\nrules: []\nrules: []\nruleset_version: "x"\n'
        path.write_text(shadowed_block, encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate"):
            load_ruleset(path)

    def test_params_models_pinned_to_known_templates(self) -> None:
        """Adversarial-review regression: KNOWN_TEMPLATES and REGISTRY were
        pinned to each other, but PARAMS_MODELS was pinned to neither — a
        held-out template added per the documented seam crashed the loader
        with a bare KeyError. All three vocabularies are the same set."""
        from auditors_trace.constraints.ruleset import PARAMS_MODELS

        assert frozenset(PARAMS_MODELS) == KNOWN_TEMPLATES

    def test_missing_params_model_is_a_validation_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defence in depth behind the pin above: even if a template name
        lands in KNOWN_TEMPLATES without a params model, the loader must
        fail as a ValidationError, never a raw KeyError."""
        import auditors_trace.constraints.ruleset as ruleset_module

        monkeypatch.setattr(ruleset_module, "KNOWN_TEMPLATES", KNOWN_TEMPLATES | {"t6_heldout"})
        with pytest.raises(ValidationError, match="t6_heldout"):
            RuleSet.model_validate(
                _ruleset([_rule(constraint_id="T6.heldout", template="t6_heldout", params={})])
            )

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

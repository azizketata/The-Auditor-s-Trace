"""Drift guards binding rules/rules.yaml to the frozen catalogues.

The template parameters are TRANSCRIBED from data/catalogue/scenario_credit.yaml
(which must never be edited — its byte hash feeds every run id), and the
constraint vocabulary, severities, and articles must stay consistent with
data/catalogue/violations.yaml. These tests make silent drift impossible: if
either catalogue and the ruleset disagree, the suite fails, not the experiment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from auditors_trace.constraints.ruleset import Rule, RuleSet, load_ruleset
from auditors_trace.scenario.catalogue import (
    ScenarioCatalogue,
    current_policy,
    load_catalogue,
    superseded_policies,
)
from auditors_trace.scenario.injector import Catalogue
from auditors_trace.scenario.injector import load_catalogue as load_violations


@pytest.fixture(scope="module")
def ruleset(repo_root: Path) -> RuleSet:
    return load_ruleset(repo_root / "rules" / "rules.yaml")


@pytest.fixture(scope="module")
def scenario(repo_root: Path) -> ScenarioCatalogue:
    return load_catalogue(repo_root / "data" / "catalogue" / "scenario_credit.yaml")


@pytest.fixture(scope="module")
def violations(repo_root: Path) -> Catalogue:
    return load_violations(repo_root / "data" / "catalogue" / "violations.yaml")


def _rule(ruleset: RuleSet, constraint_id: str) -> Rule:
    matches = [rule for rule in ruleset.rules if rule.constraint_id == constraint_id]
    assert len(matches) == 1, f"expected exactly one rule {constraint_id}"
    return matches[0]


class TestParameterProvenance:
    """Every parameter value equals the scenario catalogue value it derives from."""

    def test_t1_allowed_roles_are_the_approver_roster(
        self, ruleset: RuleSet, scenario: ScenarioCatalogue
    ) -> None:
        rule = _rule(ruleset, "T1.synchronised_approval")
        assert tuple(rule.params["allowed_roles"]) == tuple(a.role for a in scenario.approvers)

    def test_t1_decision_outcomes_are_the_approval_required_outcomes(
        self, ruleset: RuleSet, scenario: ScenarioCatalogue
    ) -> None:
        rule = _rule(ruleset, "T1.synchronised_approval")
        assert tuple(rule.params["decision_outcomes"]) == scenario.approval_required_outcomes
        # refer is the escalation outcome; listing it would make every
        # human-override session a clean-split false positive (see catalogue).
        assert "refer" not in rule.params["decision_outcomes"]

    def test_t2_required_sources_are_the_required_source_types(
        self, ruleset: RuleSet, scenario: ScenarioCatalogue
    ) -> None:
        rule = _rule(ruleset, "T2.mandatory_data_coverage")
        assert tuple(rule.params["required_sources"]) == scenario.required_source_types

    def test_t3_entrypoint_roles_are_the_entrypoint_agent(
        self, ruleset: RuleSet, scenario: ScenarioCatalogue
    ) -> None:
        rule = _rule(ruleset, "T3.delegation_integrity")
        entrypoints = tuple(a.role for a in scenario.agents if a.entrypoint)
        assert tuple(rule.params["entrypoint_roles"]) == entrypoints

    def test_t4_adverse_outcomes_match_the_catalogue(
        self, ruleset: RuleSet, scenario: ScenarioCatalogue
    ) -> None:
        rule = _rule(ruleset, "T4.reason_code_presence")
        assert tuple(rule.params["adverse_outcomes"]) == scenario.adverse_outcomes

    def test_t5_prohibited_classifications_exist_in_the_catalogue(
        self, ruleset: RuleSet, scenario: ScenarioCatalogue
    ) -> None:
        rule = _rule(ruleset, "T5.prohibited_attribute_access")
        prohibited = tuple(rule.params["prohibited_classifications"])
        assert prohibited == ("special_category",)
        catalogue_classes = {r.classification for r in scenario.resources}
        assert set(prohibited) <= catalogue_classes

    def test_every_rule_has_a_substantive_formal_statement(self, ruleset: RuleSet) -> None:
        """§8's constraint.formal is sourced from the ruleset (Phase 6): every
        rule carries a formal statement naming its real event types."""
        vocabulary = {
            "T1.synchronised_approval": "grant_approval",
            "T2.mandatory_data_coverage": "retrieve_data",
            "T3.delegation_integrity": "handoff",
            "T4.reason_code_presence": "emit_reasoning",
            "T5.prohibited_attribute_access": "lawful_basis",
            "STD.policy_version_current": "effective_to",
        }
        for rule in ruleset.rules:
            assert rule.formal.strip(), rule.constraint_id
            assert vocabulary[rule.constraint_id] in rule.formal, rule.constraint_id

    def test_std_params_bind_to_the_policy_invariants(
        self, ruleset: RuleSet, scenario: ScenarioCatalogue
    ) -> None:
        rule = _rule(ruleset, "STD.policy_version_current")
        assert rule.template == "object_absence"
        assert rule.params["anchor_event_type"] == "make_decision"
        assert rule.params["qualifier"] == "governed_by"
        assert rule.params["object_type"] == "PolicyVersion"
        assert rule.params["attribute"] == "effective_to"
        assert rule.params["violating_when"] == "non_empty"
        # The predicate is sound exactly because the catalogue guarantees it:
        # in force <=> open effective_to.
        assert current_policy(scenario).effective_to == ""
        assert all(p.effective_to != "" for p in superseded_policies(scenario))


class TestViolationCatalogueConsistency:
    def test_constraint_vocabulary_matches_detected_by(
        self, ruleset: RuleSet, violations: Catalogue
    ) -> None:
        assert {rule.constraint_id for rule in ruleset.rules} == {
            entry.constraint_id for entry in violations.entries
        }

    def test_rule_severity_matches_every_catalogue_entry_it_detects(
        self, ruleset: RuleSet, violations: Catalogue
    ) -> None:
        for entry in violations.entries:
            rule = _rule(ruleset, entry.constraint_id)
            assert rule.severity.value == entry.severity, entry.entry_id

    def test_every_catalogue_article_appears_in_the_rule_legal_basis(
        self, ruleset: RuleSet, violations: Catalogue
    ) -> None:
        for entry in violations.entries:
            rule = _rule(ruleset, entry.constraint_id)
            rule_articles = {ref.article for ref in rule.legal_basis}
            for article in entry.articles:
                match = re.search(r"Art\.\s*(\d+)", article)
                assert match is not None, article
                assert match.group(1) in rule_articles, (
                    f"{entry.entry_id} cites Art. {match.group(1)} but rule "
                    f"{rule.constraint_id} does not"
                )

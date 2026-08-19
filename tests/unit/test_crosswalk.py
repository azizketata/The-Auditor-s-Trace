"""Crosswalk loader and validator (Phase 6).

Content is a human deliverable (§11); the harness only loads and validates.
The shipped rules/crosswalk.template.yaml is all-TODO and must therefore be
UN-LOADABLE by construction — placeholder text can never reach audit evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from auditors_trace.constraints.ruleset import RuleSet, load_ruleset
from auditors_trace.evidence.crosswalk import (
    Crosswalk,
    CrosswalkError,
    legal_basis_for,
    load_crosswalk,
    standard_refs_for,
    validate_against_ruleset,
)

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "crosswalk_fixture.yaml"
TEMPLATE = REPO / "rules" / "crosswalk.template.yaml"

REQUIRED_IDS = frozenset(
    {
        "T1.synchronised_approval",
        "T2.mandatory_data_coverage",
        "T3.delegation_integrity",
        "T4.reason_code_presence",
        "T5.prohibited_attribute_access",
        "STD.policy_version_current",
    }
)


@pytest.fixture(scope="module")
def crosswalk() -> Crosswalk:
    return load_crosswalk(FIXTURE, required_ids=REQUIRED_IDS)


@pytest.fixture(scope="module")
def ruleset() -> RuleSet:
    return load_ruleset(REPO / "rules" / "rules.yaml")


class TestLoader:
    def test_fixture_loads_and_covers_the_six_ids(self, crosswalk: Crosswalk) -> None:
        assert {entry.constraint_id for entry in crosswalk.constraints} == REQUIRED_IDS
        assert crosswalk.crosswalk_version

    def test_the_shipped_template_is_unloadable(self) -> None:
        """The all-TODO template can NEVER load — placeholder rejection is
        what keeps a copy-paste out of audit evidence."""
        with pytest.raises((CrosswalkError, ValueError)):
            load_crosswalk(TEMPLATE, required_ids=REQUIRED_IDS)

    def test_unmapped_required_id_raises_naming_it(self, tmp_path: Path) -> None:
        with pytest.raises(CrosswalkError, match=r"T9\.heldout"):
            load_crosswalk(FIXTURE, required_ids=REQUIRED_IDS | {"T9.heldout"})

    def test_extra_ids_beyond_required_are_allowed(self) -> None:
        """The held-out seam: a crosswalk may map more than the shipped rules."""
        load_crosswalk(FIXTURE, required_ids=frozenset({"T1.synchronised_approval"}))

    def test_duplicate_constraint_ids_rejected(self, tmp_path: Path) -> None:
        raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
        raw["constraints"].append(raw["constraints"][0])
        path = tmp_path / "crosswalk.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        with pytest.raises((CrosswalkError, ValueError), match="duplicate"):
            load_crosswalk(path, required_ids=REQUIRED_IDS)

    def test_duplicate_yaml_keys_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "crosswalk.yaml"
        path.write_text(
            'crosswalk_version: "a"\ncrosswalk_version: "b"\nauthor: "x"\nconstraints: []\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_crosswalk(path, required_ids=frozenset())

    def test_empty_legal_basis_rejected(self, tmp_path: Path) -> None:
        raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
        raw["constraints"][0]["legal_basis"] = []
        path = tmp_path / "crosswalk.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        with pytest.raises((CrosswalkError, ValueError)):
            load_crosswalk(path, required_ids=REQUIRED_IDS)

    def test_retention_block_must_match_the_section8_constant(self, tmp_path: Path) -> None:
        raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
        raw["retention"]["min_retention_months"] = 12
        path = tmp_path / "crosswalk.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        with pytest.raises((CrosswalkError, ValueError)):
            load_crosswalk(path, required_ids=REQUIRED_IDS)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_crosswalk(tmp_path / "nope.yaml", required_ids=REQUIRED_IDS)


class TestCrossValidation:
    def test_fixture_agrees_with_the_shipped_ruleset(
        self, crosswalk: Crosswalk, ruleset: RuleSet
    ) -> None:
        validate_against_ruleset(crosswalk, ruleset)

    def test_severity_disagreement_raises(self, crosswalk: Crosswalk, ruleset: RuleSet) -> None:
        from auditors_trace.constraints.ruleset import Severity

        entries = tuple(
            entry.model_copy(update={"severity": Severity.LOW})
            if entry.constraint_id == "T1.synchronised_approval"
            else entry
            for entry in crosswalk.constraints
        )
        flipped = crosswalk.model_copy(update={"constraints": entries})
        with pytest.raises(CrosswalkError, match="severity"):
            validate_against_ruleset(flipped, ruleset)

    def test_article_set_disagreement_raises(self, crosswalk: Crosswalk, ruleset: RuleSet) -> None:
        entries = tuple(
            entry.model_copy(update={"legal_basis": entry.legal_basis[:1]})
            if entry.constraint_id == "T1.synchronised_approval"
            else entry
            for entry in crosswalk.constraints
        )
        trimmed = crosswalk.model_copy(update={"constraints": entries})
        with pytest.raises(CrosswalkError, match="citation"):
            validate_against_ruleset(trimmed, ruleset)

    def _mutated_first_basis(self, crosswalk: Crosswalk, **update: object) -> Crosswalk:
        entries = []
        for entry in crosswalk.constraints:
            if entry.constraint_id == "T1.synchronised_approval":
                basis = entry.legal_basis[0].model_copy(update=update)
                entry = entry.model_copy(update={"legal_basis": (basis, *entry.legal_basis[1:])})
            entries.append(entry)
        return crosswalk.model_copy(update={"constraints": tuple(entries)})

    def test_instrument_disagreement_raises(self, crosswalk: Crosswalk, ruleset: RuleSet) -> None:
        """Adversarial-review regression (19 Aug 2026): bare article numbers
        let a GDPR Art. 14 pass as an AI-Act Art. 14. Citations compare as
        (instrument, article, paragraph) triples."""
        flipped = self._mutated_first_basis(crosswalk, instrument="Regulation (EU) 2016/679")
        with pytest.raises(CrosswalkError, match="citation"):
            validate_against_ruleset(flipped, ruleset)

    def test_paragraph_disagreement_raises(self, crosswalk: Crosswalk, ruleset: RuleSet) -> None:
        shifted = self._mutated_first_basis(crosswalk, paragraph="999(z)")
        with pytest.raises(CrosswalkError, match="citation"):
            validate_against_ruleset(shifted, ruleset)


class TestLookups:
    def test_legal_basis_for_returns_verbatim_entries(self, crosswalk: Crosswalk) -> None:
        bases = legal_basis_for(crosswalk, "T1.synchronised_approval")
        assert bases
        assert {b.article for b in bases} == {"14", "26"}

    def test_standard_refs_for_returns_entries(self, crosswalk: Crosswalk) -> None:
        refs = standard_refs_for(crosswalk, "T5.prohibited_attribute_access")
        assert refs

    def test_unmapped_lookup_raises(self, crosswalk: Crosswalk) -> None:
        with pytest.raises(CrosswalkError, match="T9"):
            legal_basis_for(crosswalk, "T9.heldout")
        with pytest.raises(CrosswalkError, match="T9"):
            standard_refs_for(crosswalk, "T9.heldout")

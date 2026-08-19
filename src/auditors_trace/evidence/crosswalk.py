"""Load and validate ``rules/crosswalk.yaml``, the article and standard mapping.

Phase 6 deliverable, but only the loader and the validator. The crosswalk
*content* is Alina's deliverable (section 11); the harness must never generate
it. Articles 12, 14, 26, 72 plus ISO/IEC 24970, ISO/IEC 42001, NIST AI RMF.

Until the human-authored file lands, the only loadable crosswalk is the test
fixture (``tests/fixtures/crosswalk_fixture.yaml``); the shipped all-TODO
template is un-loadable by construction — every text field is
substance-validated, so placeholder content can never reach audit evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from auditors_trace.constraints.ruleset import (
    CONSTRAINT_ID_PATTERN,
    RuleSet,
    Severity,
    StrictKeyLoader,
    substantive_text,
)
from auditors_trace.evidence.record import RETENTION, LegalBasis, Retention, StandardRef

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class CrosswalkError(ValueError):
    """The crosswalk is malformed, incomplete, or disagrees with the ruleset."""


class CrosswalkEntry(BaseModel):
    """One constraint's legal basis, standard references, and statement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    constraint_id: str = Field(pattern=CONSTRAINT_ID_PATTERN)
    natural_language: str = Field(min_length=1)
    legal_basis: tuple[LegalBasis, ...] = Field(min_length=1)  # invariant I3
    standard_refs: tuple[StandardRef, ...] = Field(min_length=1)
    severity: Severity

    @field_validator("natural_language")
    @classmethod
    def _substantive(cls, value: str) -> str:
        return substantive_text(value)


class Crosswalk(BaseModel):
    """Constraint id to legal basis and standard references."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    crosswalk_version: str = Field(min_length=1)
    author: str = Field(min_length=1)
    constraints: tuple[CrosswalkEntry, ...] = Field(min_length=1)
    #: Optional; when present it must equal section 8's retention constant.
    retention: Retention | None = None

    @field_validator("crosswalk_version", "author")
    @classmethod
    def _substantive(cls, value: str) -> str:
        return substantive_text(value)

    @model_validator(mode="after")
    def _coherent(self) -> Crosswalk:
        ids = [entry.constraint_id for entry in self.constraints]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate constraint ids: {duplicates}")
        if self.retention is not None and self.retention != RETENTION:
            raise ValueError(
                "the crosswalk retention block must equal section 8's retention "
                "constant field-for-field"
            )
        return self


def load_crosswalk(path: Path, *, required_ids: Iterable[str]) -> Crosswalk:
    """Load and validate the crosswalk. Raises on any unmapped constraint id.

    ``required_ids`` is the constraint vocabulary the caller evaluates
    (normally the ruleset's ids): every one must be mapped (invariant I3).
    Extra crosswalk entries beyond ``required_ids`` are allowed — the
    held-out seam may map more than the shipped rules.
    """
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.load(text, Loader=StrictKeyLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise CrosswalkError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CrosswalkError(f"{path.name}: top level is not a mapping")
    try:
        crosswalk = Crosswalk.model_validate(raw)
    except ValidationError as exc:
        raise CrosswalkError(f"{path.name}: {exc}") from exc
    mapped = {entry.constraint_id for entry in crosswalk.constraints}
    missing = sorted(set(required_ids) - mapped)
    if missing:
        raise CrosswalkError(
            f"{path.name}: unmapped constraint ids (invariant I3): {', '.join(missing)}"
        )
    return crosswalk


def _entry(crosswalk: Crosswalk, constraint_id: str) -> CrosswalkEntry:
    for entry in crosswalk.constraints:
        if entry.constraint_id == constraint_id:
            return entry
    raise CrosswalkError(f"constraint {constraint_id!r} is not mapped in the crosswalk")


def legal_basis_for(crosswalk: Crosswalk, constraint_id: str) -> list[LegalBasis]:
    """Return the legal bases for a constraint. Never empty; raises if unmapped."""
    return list(_entry(crosswalk, constraint_id).legal_basis)


def standard_refs_for(crosswalk: Crosswalk, constraint_id: str) -> list[StandardRef]:
    """Return the standard and framework references for a constraint."""
    return list(_entry(crosswalk, constraint_id).standard_refs)


def validate_against_ruleset(crosswalk: Crosswalk, ruleset: RuleSet) -> None:
    """Cross-validate the two hand-authored sources; disagreement is an error.

    For every rule: a crosswalk entry exists, severities are equal, and the
    legal-basis article SETS are equal. Requirement texts may legitimately
    differ in wording (both are human-authored); the articles may not.
    """
    for rule in ruleset.rules:
        entry = _entry(crosswalk, rule.constraint_id)
        if entry.severity is not rule.severity:
            raise CrosswalkError(
                f"{rule.constraint_id}: crosswalk severity {entry.severity.value!r} "
                f"disagrees with the ruleset's {rule.severity.value!r}"
            )
        crosswalk_articles = {basis.article for basis in entry.legal_basis}
        ruleset_articles = {reference.article for reference in rule.legal_basis}
        if crosswalk_articles != ruleset_articles:
            raise CrosswalkError(
                f"{rule.constraint_id}: crosswalk article set {sorted(crosswalk_articles)} "
                f"disagrees with the ruleset's {sorted(ruleset_articles)}"
            )

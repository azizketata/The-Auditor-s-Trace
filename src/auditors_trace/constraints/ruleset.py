"""Load and validate ``rules/rules.yaml``.

Phase 5 deliverable. Invariant I3 is enforced here: a rule without a legal
article reference is a load-time error, by design. Do not relax this.

The template parameter models live here too, so ``templates.py`` can validate
``rule.params`` into typed access without a circular import: this module knows
template NAMES only (:data:`KNOWN_TEMPLATES`), never template code. The engine
registry is pinned 1:1 to that name set by test, and a held-out template later
means one new name here plus one registered function — nothing else moves.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier, o2o_endpoints

if TYPE_CHECKING:
    from collections.abc import Mapping


class Severity(StrEnum):
    """Violation severity, as carried into the evidence record."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


#: Registered template function names — the closed vocabulary `Rule.template`
#: draws from. Names, not callables: `engine.REGISTRY` maps them to functions
#: and a test pins the two sets equal, so they cannot drift.
KNOWN_TEMPLATES: Final[frozenset[str]] = frozenset(
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

#: Mirrors ``scenario.injector._CONSTRAINT_ID_PATTERN``. The loader checks
#: shape only; the closed six-id vocabulary is pinned by the content tests,
#: keeping the held-out extension seam open (section 7a).
_CONSTRAINT_ID_PATTERN: Final = r"^[A-Z][A-Z0-9]*\.[a-z][a-z0-9_]*$"

#: An article must actually be named — hard rule 4 is about substance, so a
#: placeholder like "TBD" fails the pattern at load time.
_ARTICLE_PATTERN: Final = r"^\d+[0-9a-z]*$"


def _substantive(value: str) -> str:
    lowered = value.strip().lower()
    if not lowered or "todo" in lowered or lowered == "tbd":
        raise ValueError(f"placeholder text {value!r} is not a legal reference (hard rule 4)")
    return value


class LegalReference(BaseModel):
    """A citation into a legal instrument. Required on every rule (invariant I3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: str = Field(min_length=1)
    article: str = Field(pattern=_ARTICLE_PATTERN)
    paragraph: str = ""
    requirement: str = Field(min_length=1)

    @field_validator("instrument", "requirement")
    @classmethod
    def _no_placeholders(cls, value: str) -> str:
        return _substantive(value)


class _Params(BaseModel):
    """Base for the per-template parameter models: frozen, closed key set."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_event_type(value: str) -> str:
    try:
        EventType(value)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an OCEL event type") from exc
    return value


def _require_object_type(value: str) -> str:
    try:
        ObjectType(value)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an OCEL object type") from exc
    return value


class T1Params(_Params):
    """SynchronisedApproval: which outcomes need approval, by which roles."""

    allowed_roles: tuple[str, ...] = Field(min_length=1)
    decision_outcomes: tuple[str, ...] = Field(min_length=1)


class T2Params(_Params):
    """MandatoryDataCoverage: the resource types every decision must rest on."""

    required_sources: tuple[str, ...] = Field(min_length=1)


class T3Params(_Params):
    """DelegationIntegrity: the roles allowed to act without a prior handoff."""

    entrypoint_roles: tuple[str, ...] = Field(min_length=1)


class T4Params(_Params):
    """ReasonCodePresence: the outcomes that owe the applicant an explanation."""

    adverse_outcomes: tuple[str, ...] = Field(min_length=1)


class T5Params(_Params):
    """ProhibitedAttributeAccess: classifications needing a lawful basis."""

    prohibited_classifications: tuple[str, ...] = Field(min_length=1)


class ObjectAbsenceParams(_Params):
    """Attribute-predicated absence over one qualified E2O relation.

    ``violating_when: always`` is classic DECLARE object-absence (any such
    relation violates); ``non_empty``/``equals`` violate only when the target
    object's ``attribute`` matches the predicate — the form
    ``STD.policy_version_current`` uses (a closed ``effective_to`` marks a
    superseded policy).
    """

    anchor_event_type: str
    qualifier: str
    object_type: str
    attribute: str = ""
    violating_when: Literal["always", "non_empty", "equals"]
    value: str = ""

    @field_validator("anchor_event_type")
    @classmethod
    def _event_type(cls, value: str) -> str:
        return _require_event_type(value)

    @field_validator("object_type")
    @classmethod
    def _object_type(cls, value: str) -> str:
        return _require_object_type(value)

    @field_validator("qualifier")
    @classmethod
    def _semantic_e2o_qualifier(cls, value: str) -> str:
        try:
            qualifier = Qualifier(value)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a relation qualifier") from exc
        if qualifier is Qualifier.DECLARES:
            raise ValueError("'declares' is materialisation, never semantic evidence")
        try:
            o2o_endpoints(qualifier)
        except ValueError:
            return value
        raise ValueError(f"{value!r} is an o2o qualifier; the anchor is an event")

    @model_validator(mode="after")
    def _predicate_needs_attribute(self) -> ObjectAbsenceParams:
        if self.violating_when != "always" and not self.attribute:
            raise ValueError(f"violating_when={self.violating_when!r} needs an attribute")
        if self.violating_when != "equals" and self.value:
            raise ValueError("value is only meaningful with violating_when='equals'")
        return self


class ObjectExistenceParams(_Params):
    """Each anchor object must have a matching qualified o2o relation."""

    anchor_object_type: str
    related_object_type: str
    o2o_qualifier: str
    direction: Literal["outgoing", "incoming"] = "outgoing"

    @field_validator("anchor_object_type", "related_object_type")
    @classmethod
    def _object_type(cls, value: str) -> str:
        return _require_object_type(value)

    @field_validator("o2o_qualifier")
    @classmethod
    def _o2o(cls, value: str) -> str:
        o2o_endpoints(Qualifier(value))  # raises on non-o2o qualifiers
        return value


class SynchronisedActivitiesParams(_Params):
    """Response/precedence between two activities on a shared object."""

    activity_a: str
    activity_b: str
    object_type: str

    @field_validator("activity_a", "activity_b")
    @classmethod
    def _event_type(cls, value: str) -> str:
        return _require_event_type(value)

    @field_validator("object_type")
    @classmethod
    def _object_type(cls, value: str) -> str:
        return _require_object_type(value)

    @model_validator(mode="after")
    def _distinct_activities(self) -> SynchronisedActivitiesParams:
        if self.activity_a == self.activity_b:
            raise ValueError("activity_a and activity_b must differ")
        return self


#: Template name -> its parameter model. Every rule's ``params`` mapping is
#: validated against this at load time (and re-validated by the template at
#: evaluation time for typed access).
PARAMS_MODELS: Final[Mapping[str, type[_Params]]] = MappingProxyType(
    {
        "t1_synchronised_approval": T1Params,
        "t2_mandatory_data_coverage": T2Params,
        "t3_delegation_integrity": T3Params,
        "t4_reason_code_presence": T4Params,
        "t5_prohibited_attribute_access": T5Params,
        "object_existence": ObjectExistenceParams,
        "object_absence": ObjectAbsenceParams,
        "synchronised_response": SynchronisedActivitiesParams,
        "synchronised_precedence": SynchronisedActivitiesParams,
    }
)


class Rule(BaseModel):
    """One instantiated constraint template with its parameters and legal basis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    constraint_id: str = Field(pattern=_CONSTRAINT_ID_PATTERN)
    template: str
    description: str = Field(min_length=1)
    severity: Severity
    params: dict[str, object]
    legal_basis: tuple[LegalReference, ...] = Field(min_length=1)  # invariant I3

    @field_validator("template")
    @classmethod
    def _known_template(cls, value: str) -> str:
        if value not in KNOWN_TEMPLATES:
            raise ValueError(f"unknown template {value!r}; known: {sorted(KNOWN_TEMPLATES)}")
        return value

    @model_validator(mode="after")
    def _params_fit_the_template(self) -> Rule:
        PARAMS_MODELS[self.template].model_validate(self.params)
        return self


class RuleSet(BaseModel):
    """A versioned collection of rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    ruleset_version: str = Field(min_length=1)
    rules: tuple[Rule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _constraint_ids_unique(self) -> RuleSet:
        ids = [rule.constraint_id for rule in self.rules]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate constraint ids: {duplicates}")
        return self


def load_ruleset(path: Path) -> RuleSet:
    """Load and validate a ruleset. Raises if any rule lacks an article reference."""
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: top level is not a mapping")
    return RuleSet.model_validate(raw)


def ruleset_version(ruleset: RuleSet) -> str:
    """Return the version string cited by every evidence record this ruleset produces."""
    return ruleset.ruleset_version

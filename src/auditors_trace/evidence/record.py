"""The evidence record schema.

Phase 6 deliverable. Builds to BUILD-PLAN.md section 8 exactly: the field
inventory below is section 8's and nothing more — no ``schema_version``, no
``session_id``, no ``detail`` (those are renderer lookup inputs, never record
fields). Every free-text field is substance-validated: placeholder text can
never reach audit evidence.

``generated_at`` may exist as a field but is excluded from every hash: no
wall-clock value enters any hashed payload (invariant I1). The record's own
hash lives ONLY in ``integrity``, which is excluded from hashing (B12); a
record is hash-chained and tamper-evident, never "signed" unless an actual
signature ships.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from auditors_trace.constraints.ruleset import (
    ARTICLE_PATTERN,
    CONSTRAINT_ID_PATTERN,
    Severity,
    substantive_text,
)

_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
_SHORT_HASH_PATTERN: Final = r"^[0-9a-f]{16}$"
_TRACE_ID_PATTERN: Final = r"^[0-9a-f]{32}$"


class _Frozen(BaseModel):
    """Every record component: immutable, closed key set (section 8 exact)."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class Constraint(_Frozen):
    """The constraint that fired: id, statements, and the ruleset that carried it."""

    id: str = Field(pattern=CONSTRAINT_ID_PATTERN)
    natural_language: str = Field(min_length=1)
    formal: str = Field(min_length=1)
    ruleset_version: str = Field(min_length=1)

    @field_validator("natural_language", "formal", "ruleset_version")
    @classmethod
    def _substantive(cls, value: str) -> str:
        return substantive_text(value)


class LegalBasis(_Frozen):
    """Instrument, article, paragraph, and the requirement text it imposes."""

    instrument: str = Field(min_length=1)
    article: str = Field(pattern=ARTICLE_PATTERN)
    paragraph: str = ""
    requirement: str = Field(min_length=1)

    @field_validator("instrument", "requirement")
    @classmethod
    def _substantive(cls, value: str) -> str:
        return substantive_text(value)

    @field_validator("paragraph")
    @classmethod
    def _optional_but_substantive(cls, value: str) -> str:
        if value:
            substantive_text(value)
        return value


class StandardRef(_Frozen):
    """A reference into ISO/IEC 24970, ISO/IEC 42001, or the NIST AI RMF.

    Section 8's shape is heterogeneous: exactly one of
    ``{standard, clause}`` | ``{standard, control}`` | ``{framework, function}``
    is populated, and serialisation emits only the populated pair so no empty
    key ever enters canonical JSON.
    """

    standard: str = ""
    clause: str = ""
    control: str = ""
    framework: str = ""
    function: str = ""

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> StandardRef:
        clause_shape = bool(self.standard) and bool(self.clause)
        control_shape = bool(self.standard) and bool(self.control)
        framework_shape = bool(self.framework) and bool(self.function)
        populated = [name for name in type(self).model_fields if getattr(self, name)]
        shapes = {
            "clause": clause_shape and populated == ["standard", "clause"],
            "control": control_shape and sorted(populated) == ["control", "standard"],
            "framework": framework_shape and sorted(populated) == ["framework", "function"],
        }
        if sum(shapes.values()) != 1:
            raise ValueError(
                "a standard reference is exactly one of {standard+clause | "
                f"standard+control | framework+function}}; got fields {sorted(populated)}"
            )
        for name in populated:
            substantive_text(getattr(self, name))
        return self

    @model_serializer(mode="plain")
    def _only_the_populated_pair(self) -> dict[str, str]:
        if self.clause:
            return {"standard": self.standard, "clause": self.clause}
        if self.control:
            return {"standard": self.standard, "control": self.control}
        return {"framework": self.framework, "function": self.function}


class EvidenceLocator(_Frozen):
    """The OCEL event/object ids and OTel trace/span ids the violation rests on.

    ``otel_trace_id`` is singular by design: every engine violation cites
    events of a single session, hence a single trace. ``otel_span_ids`` are
    the cited events' OWN spans in citation order — never enrichment spans
    (B1: a record must cite only spans that exhibit the violation).
    """

    ocel_event_ids: tuple[str, ...] = Field(min_length=1)
    ocel_object_ids: tuple[str, ...] = ()
    otel_trace_id: str = Field(pattern=_TRACE_ID_PATTERN)
    otel_span_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("otel_span_ids")
    @classmethod
    def _span_id_shapes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        import re

        for span_id in value:
            if not re.fullmatch(r"[0-9a-f]{16}", span_id):
                raise ValueError(f"{span_id!r} is not a 16-hex OTel span id")
        return value

    @model_validator(mode="after")
    def _one_span_per_cited_event(self) -> EvidenceLocator:
        if len(self.otel_span_ids) != len(self.ocel_event_ids):
            raise ValueError(
                f"{len(self.ocel_event_ids)} cited events but "
                f"{len(self.otel_span_ids)} span ids; each event has exactly its own span"
            )
        if len(set(self.otel_span_ids)) != len(self.otel_span_ids):
            raise ValueError("duplicate otel span ids in one record")
        return self


class Context(_Frozen):
    """Session context: the governing policy and component versions.

    Empty when the violation's session is unknown (a flattened baseline or
    judge output carries no session).
    """

    policy_version: str = ""
    model_versions: dict[str, str] = {}
    agent_versions: dict[str, str] = {}


class Provenance(_Frozen):
    """Engine version, engine commit, and the input log hash."""

    engine_version: str = Field(min_length=1)
    engine_commit: str = ""
    input_log_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("engine_version")
    @classmethod
    def _substantive(cls, value: str) -> str:
        return substantive_text(value)


class Reproducibility(_Frozen):
    """The rerun command an auditor executes to reproduce this record byte-for-byte.

    Exactly one field: the record's own hash lives only in ``integrity``
    (excluded from hashing) — the pre-B12 ``expected_record_sha256`` was a
    fixed-point impossibility and does not exist.
    """

    rerun_command: str = Field(min_length=1)

    @field_validator("rerun_command")
    @classmethod
    def _substantive(cls, value: str) -> str:
        return substantive_text(value)


class Integrity(_Frozen):
    """Chain index, this record's hash, and the previous record's hash."""

    chain_index: int = Field(ge=0)
    record_sha256: str = Field(pattern=_SHA256_PATTERN)
    prev_record_sha256: str = Field(pattern=_SHA256_PATTERN)


class Retention(_Frozen):
    """Retention class and minimum period, per Article 26(6).

    Literal-pinned to section 8's exact wording (PLAN-REVIEW A4: safe as
    specified); serialised under the JSON key ``class``.
    """

    retention_class: Literal["ai_act_art_26_6"] = Field(alias="class")
    min_retention_months: Literal[6]
    note: Literal["financial institutions: retain per applicable Union financial services law"]


#: The one retention block every record carries (section 8, verbatim).
RETENTION: Final = Retention.model_validate(
    {
        "class": "ai_act_art_26_6",
        "min_retention_months": 6,
        "note": "financial institutions: retain per applicable Union financial services law",
    }
)


class EvidenceRecord(_Frozen):
    """One violation rendered as defensible, reproducible audit evidence."""

    violation_id: str = Field(pattern=_SHORT_HASH_PATTERN)
    constraint: Constraint
    legal_basis: tuple[LegalBasis, ...] = Field(min_length=1)  # invariant I3
    standard_refs: tuple[StandardRef, ...] = ()
    severity: Severity
    evidence: EvidenceLocator
    context: Context
    provenance: Provenance
    reproducibility: Reproducibility
    #: ``None`` until ``chain.chain()`` fills it; excluded from hashing (B12).
    integrity: Integrity | None = None
    retention: Retention = RETENTION
    #: May exist for display; excluded from every hash (invariant I1).
    generated_at: str | None = None

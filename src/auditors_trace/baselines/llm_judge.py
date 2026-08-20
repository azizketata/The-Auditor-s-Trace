"""LLM-as-judge baseline.

Phase 7 deliverable. The second and last place an LLM may appear (invariant
I2). Three pinned Anthropic models x five repeated samples per input — the
measured quantity is the end-to-end run-to-run variability of the deployed
service (no vendor seed exists; sampling parameters are rejected by current
frontier models, so provider defaults are used and recorded).

Every response is cached to disk keyed by (model, sample index, condition,
input hash, prompt hash) so the evaluation replays without API access; the
cache is committed (invariant I5). Output is parsed against a fixed schema
and otherwise left alone: parse failures count as errors and are reported,
never cleaned up or normalised away.

Pre-freeze safety: no judge run may target the evaluation base run
(``EVALUATION_BASE_RUN_ID``) until the ``catalogue-v1`` freeze exists — the
:class:`EvaluationLockError` guard enforces that structurally, not by
discipline.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auditors_trace.constraints.ruleset import (
    RuleSet,
    StrictKeyLoader,
    load_ruleset,
    substantive_text,
)
from auditors_trace.constraints.templates import Violation, build_index, violation_sort_key
from auditors_trace.eval.metrics import verdict_set_key
from auditors_trace.model.log import OCELLog, canonical_json

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from auditors_trace.eval.metrics import JudgeMatching

#: The three pinned judge models (user decision, Phase 7 amendment): three
#: Anthropic capability tiers; exact version strings and access dates are
#: recorded from the Models API into each cache record's provenance.
JUDGE_MODELS: Final = ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5")

Condition = Literal["spans", "ocel"]
CONDITIONS: Final = ("spans", "ocel")

#: The base run the three evaluation splits partition. Judging it is locked
#: until the catalogue-v1 freeze (I4: prompts + matcher frozen BEFORE any
#: detection experiment runs).
EVALUATION_BASE_RUN_ID: Final = "f95aed68e789bb84"

DEFAULT_CACHE_DIR: Final = Path("data") / "generated" / "judge_cache"
DEFAULT_PROMPTS_PATH: Final = Path("rules") / "judge_prompts.yaml"


class JudgeError(RuntimeError):
    """The judge harness cannot proceed."""


class EvaluationLockError(JudgeError):
    """A judge run targeted the evaluation base run before the freeze."""


class JudgeProviderUnavailableError(JudgeError):
    """The live judge provider cannot be used in this environment."""


# ---------------------------------------------------------------------------
# Output schema — parsed, never repaired
# ---------------------------------------------------------------------------


class JudgeClaim(BaseModel):
    """One violation the judge asserts, in its own words."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    constraint_id: str = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)
    explanation: str = ""


class JudgeReport(BaseModel):
    """The judge's full structured answer for one session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    violations: list[JudgeClaim]


def judge_schema() -> dict[str, object]:
    """The JSON schema sent as the provider's structured-output format.

    Flat (no ``$defs``), ``additionalProperties: false`` throughout — the
    same shape :class:`JudgeReport` validates, so a schema-conforming
    response always parses.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["violations"],
        "properties": {
            "violations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["constraint_id", "event_ids", "explanation"],
                    "properties": {
                        "constraint_id": {"type": "string", "minLength": 1},
                        "event_ids": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "explanation": {"type": "string"},
                    },
                },
            }
        },
    }


# ---------------------------------------------------------------------------
# Prompts — a freeze-bundle item, loaded and rendered, never hand-copied
# ---------------------------------------------------------------------------

_PLACEHOLDERS: Final[dict[str, frozenset[str]]] = {
    "system_template": frozenset({"rules_block", "output_schema"}),
    "user_template_spans": frozenset({"session_id", "payload"}),
    "user_template_ocel": frozenset({"session_id", "payload"}),
}


class JudgePrompts(BaseModel):
    """The verbatim judge prompts (I4 freeze item)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    prompt_version: str = Field(min_length=1)
    system_template: str = Field(min_length=1)
    user_template_spans: str = Field(min_length=1)
    user_template_ocel: str = Field(min_length=1)

    @field_validator("prompt_version")
    @classmethod
    def _substantive_version(cls, value: str) -> str:
        return substantive_text(value)

    @field_validator("system_template", "user_template_spans", "user_template_ocel")
    @classmethod
    def _placeholders_exact(cls, value: str, info: object) -> str:
        import string

        field_name = info.field_name  # type: ignore[attr-defined]
        found = {
            name
            for _, name, _, _ in string.Formatter().parse(value)
            if name is not None and name != ""
        }
        expected = _PLACEHOLDERS[field_name]
        if found != expected:
            raise ValueError(
                f"{field_name} placeholders {sorted(found)} != expected {sorted(expected)}"
            )
        return value


def load_judge_prompts(path: Path) -> JudgePrompts:
    """Load and validate the prompts file (strict keys, exact placeholders)."""
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.load(text, Loader=StrictKeyLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: top level is not a mapping")
    return JudgePrompts.model_validate(raw)


def render_rules_block(ruleset: RuleSet) -> str:
    """The constraints block the judge sees, generated from the ruleset.

    Rendered from ``load_ruleset(rules/rules.yaml)`` at request time — never
    hand-copied into the prompt file (drift-guarded by test). One entry per
    rule: id, severity, articles, description, formal statement.
    """
    lines: list[str] = []
    for rule in sorted(ruleset.rules, key=lambda r: r.constraint_id):
        articles = "; ".join(
            f"{reference.instrument} Art. {reference.article}"
            + (f"({reference.paragraph})" if reference.paragraph else "")
            for reference in rule.legal_basis
        )
        lines.append(f"- id: {rule.constraint_id} | severity: {rule.severity.value}")
        lines.append(f"  articles: {articles}")
        lines.append(f"  rule: {rule.description}")
        lines.append(f"  formally: {rule.formal}")
    return "\n".join(lines)


def render_prompt(
    prompts: JudgePrompts,
    ruleset: RuleSet,
    *,
    condition: Condition,
    session_id: str,
    payload: str,
) -> tuple[str, str]:
    """Return (system_text, user_text) for one judging call."""
    system_text = prompts.system_template.format(
        rules_block=render_rules_block(ruleset),
        output_schema=canonical_json(judge_schema()),
    )
    template = prompts.user_template_spans if condition == "spans" else prompts.user_template_ocel
    user_text = template.format(session_id=session_id, payload=payload)
    return system_text, user_text


def prompt_sha256(prompts: JudgePrompts, ruleset: RuleSet) -> str:
    """Content hash of everything frozen about the prompt.

    Covers the rendered system text (rules block included) and both user
    templates pre-payload — a change to any frozen prompt content changes
    every cache key.
    """
    system_text, _ = render_prompt(
        prompts, ruleset, condition="spans", session_id="{session_id}", payload="{payload}"
    )
    material = "\x00".join((system_text, prompts.user_template_spans, prompts.user_template_ocel))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Inputs — the two mandated conditions
# ---------------------------------------------------------------------------


def spans_payload(split_dir: Path, session_id: str) -> str:
    """The raw-span condition: the session's JSONL file text, verbatim."""
    return (split_dir / f"{session_id}.jsonl").read_text(encoding="utf-8")


def session_excerpt(log: OCELLog, session_id: str) -> str:
    """The serialized-OCEL condition: one session as deterministic JSON.

    Events (chronological, with attributes and qualified relations) followed
    by every referenced object with its attributes. The PRE-REGISTERED
    foreign-object-stub rule: an object also referenced by another session's
    events carries ``"foreign": true`` — so a cross-session relation (V2's
    repointed ``approves``) is visible to the judge by construction.
    """
    index = build_index(log)
    events = [
        event
        for event in index.events
        if index.session_of_event.get(event.event_id) == session_id
        or any(relation.object_id == session_id for relation in event.relations)
    ]
    if not events:
        raise JudgeError(f"no events reference session {session_id!r}")

    referenced: list[str] = []
    seen: set[str] = set()
    for event in events:
        for relation in event.relations:
            if relation.object_id not in seen:
                seen.add(relation.object_id)
                referenced.append(relation.object_id)

    session_event_ids = {event.event_id for event in events}
    foreign: set[str] = set()
    for event in index.events:
        if event.event_id in session_event_ids:
            continue
        for relation in event.relations:
            if relation.object_id in seen:
                foreign.add(relation.object_id)

    payload = {
        "session_id": session_id,
        "events": [
            {
                "id": event.event_id,
                "type": event.event_type.value,
                "timestamp": event.timestamp,
                "attributes": dict(event.attributes),
                "relations": [
                    {"object_id": relation.object_id, "qualifier": relation.qualifier.value}
                    for relation in event.relations
                ],
            }
            for event in events
        ],
        "objects": [
            {
                "id": object_id,
                "type": index.type_of_object[object_id].value,
                "attributes": index.object_attrs.get(object_id, {}),
                **({"foreign": True} if object_id in foreign else {}),
            }
            for object_id in sorted(referenced)
            if object_id in index.type_of_object
        ],
    }
    return canonical_json(payload)


def input_hash(payload: str) -> str:
    """sha256 of the exact payload text the judge sees."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Client seam — anthropic (live) | scripted (hermetic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    """Everything needed to issue (or replay) one judging call."""

    model_id: str
    sample_index: int
    condition: Condition
    session_id: str
    system_text: str
    user_text: str
    schema: Mapping[str, object]
    #: Real event ids in the payload — lets the scripted client cite genuine
    #: anchors without parsing the payload.
    candidate_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RawJudgeResponse:
    """One provider response plus its volatile provenance (cache-only fields)."""

    text: str
    model_version: str
    display_name: str
    model_created_at: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    structured_mode: str
    stop_reason: str


class JudgeClient(Protocol):
    """The provider seam (mirrors scenario.models.build_chat_model's shape)."""

    def complete(self, request: JudgeRequest) -> RawJudgeResponse: ...


class AnthropicJudgeClient:
    """The live client: raw SDK, native structured outputs, no sampling params.

    Requires ``ANTHROPIC_API_KEY`` and the ``[judge]`` extra; both absences
    raise :class:`JudgeProviderUnavailableError` with an actionable message.
    Latency and access dates are measured here and recorded ONLY in cache
    records — they never reach a hashed artifact.
    """

    def __init__(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise JudgeProviderUnavailableError(
                "the live judge requires ANTHROPIC_API_KEY in the environment; "
                "set it, or use provider='scripted'"
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise JudgeProviderUnavailableError(
                "the live judge requires the anthropic package; "
                "install the [judge] extra (uv sync --extra judge)"
            ) from exc
        self._client = anthropic.Anthropic(timeout=120.0)
        self._model_info: dict[str, tuple[str, str, str]] = {}

    def _provenance(self, model_id: str) -> tuple[str, str, str]:
        if model_id not in self._model_info:
            info = self._client.models.retrieve(model_id)
            self._model_info[model_id] = (
                info.id,
                info.display_name,
                info.created_at.isoformat(),
            )
        return self._model_info[model_id]

    def complete(self, request: JudgeRequest) -> RawJudgeResponse:
        version, display_name, created_at = self._provenance(request.model_id)
        started = time.perf_counter()
        response = self._client.messages.create(
            model=request.model_id,
            max_tokens=4096,
            system=request.system_text,
            messages=[{"role": "user", "content": request.user_text}],
            output_config={"format": {"type": "json_schema", "schema": dict(request.schema)}},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        text = "".join(block.text for block in response.content if block.type == "text")
        return RawJudgeResponse(
            text=text,
            model_version=version,
            display_name=display_name,
            model_created_at=created_at,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            structured_mode="output_config.json_schema",
            stop_reason=str(response.stop_reason),
        )


def _default_script(request: JudgeRequest) -> str:
    """Content-derived scripted verdict (ScriptedCreditModel precedent).

    The digest of (payload, sample_index) picks 0-2 claims with closed-
    vocabulary constraint ids and REAL event ids from the request — so five
    samples genuinely differ and hermetic variance is measurable.
    """
    vocabulary = (
        "T1.synchronised_approval",
        "T2.mandatory_data_coverage",
        "T3.delegation_integrity",
        "T4.reason_code_presence",
        "T5.prohibited_attribute_access",
        "STD.policy_version_current",
    )
    digest = hashlib.sha256(f"{request.user_text}\x00{request.sample_index}".encode()).digest()
    count = digest[0] % 3
    claims = []
    for position in range(count):
        constraint = vocabulary[digest[1 + position] % len(vocabulary)]
        anchor = request.candidate_event_ids[
            digest[4 + position] % len(request.candidate_event_ids)
        ]
        claims.append(
            {
                "constraint_id": constraint,
                "event_ids": [anchor],
                "explanation": f"scripted verdict {position}",
            }
        )
    return json.dumps({"violations": claims}, sort_keys=True)


class ScriptedJudgeClient:
    """Hermetic judge: deterministic, content-derived, no network."""

    def __init__(self, script_fn: Callable[[JudgeRequest], str] = _default_script) -> None:
        self._script_fn = script_fn

    def complete(self, request: JudgeRequest) -> RawJudgeResponse:
        text = self._script_fn(request)
        return RawJudgeResponse(
            text=text,
            model_version=f"scripted-{request.model_id}",
            display_name="scripted judge",
            model_created_at="1970-01-01T00:00:00",
            input_tokens=max(1, len(request.user_text) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=0,
            structured_mode="scripted",
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Cache — committed; the replay path constructs no client
# ---------------------------------------------------------------------------


def cache_key(
    model_id: str, sample_index: int, condition: Condition, payload_hash: str, prompt_hash: str
) -> str:
    """The response cache key (BUILD-PLAN: model, sample index, input hash —
    plus condition and prompt hash so neither can silently alias)."""
    return hashlib.sha256(
        canonical_json(
            {
                "condition": condition,
                "input_hash": payload_hash,
                "model_id": model_id,
                "prompt_sha256": prompt_hash,
                "sample_index": sample_index,
            }
        ).encode("utf-8")
    ).hexdigest()


def _write_cache_record(
    path: Path,
    *,
    request: JudgeRequest,
    response: RawJudgeResponse,
    payload_hash: str,
    prompt_hash: str,
    prompt_version: str,
    parsed: JudgeReport | None,
    schema_failure: bool,
) -> None:
    import datetime

    record = {
        "fingerprint": {
            "access_date": datetime.date.today().isoformat(),
            "condition": request.condition,
            "input_hash": payload_hash,
            "model_created_at": response.model_created_at,
            "model_display_name": response.display_name,
            "model_id": request.model_id,
            "model_version": response.model_version,
            "prompt_sha256": prompt_hash,
            "prompt_version": prompt_version,
            "sample_index": request.sample_index,
            "sampling": "provider_default",
            "session_id": request.session_id,
            "structured_mode": response.structured_mode,
        },
        "latency_ms": response.latency_ms,
        "response": {
            "parsed": None if parsed is None else parsed.model_dump(mode="json"),
            "raw_text": response.text,
            "schema_failure": schema_failure,
            "stop_reason": response.stop_reason,
        },
        "usage": {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    temporary.write_text(
        json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _parse_report(text: str) -> tuple[JudgeReport | None, bool]:
    """Parse or report — never repair (the B5 contract)."""
    try:
        return JudgeReport.model_validate(json.loads(text)), False
    except Exception:
        return None, True


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgeRun:
    """One (model, sample, condition, session) pass — volatile-free by design.

    Cost, latency, and access dates live ONLY in the cache record this run's
    ``cache_key`` names (see :class:`CostRecord`), so nothing volatile can
    leak into a hashed artifact through a JudgeRun.
    """

    model_id: str
    sample_index: int
    condition: Condition
    session_id: str
    violations: tuple[Violation, ...]
    schema_failure: bool
    cache_key: str
    from_cache: bool


@dataclass(frozen=True, slots=True)
class CostRecord:
    """Per-response cost and latency, read back from the committed cache."""

    model_id: str
    model_version: str
    sample_index: int
    condition: Condition
    session_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    access_date: str


def cost_records(cache_dir: Path, keys: Iterable[str]) -> tuple[CostRecord, ...]:
    """Load the cost/latency sidecar data for the given cache keys."""
    records: list[CostRecord] = []
    for key in keys:
        raw = json.loads((cache_dir / f"{key}.json").read_text(encoding="utf-8"))
        fingerprint = raw["fingerprint"]
        records.append(
            CostRecord(
                model_id=fingerprint["model_id"],
                model_version=fingerprint["model_version"],
                sample_index=fingerprint["sample_index"],
                condition=fingerprint["condition"],
                session_id=fingerprint["session_id"],
                input_tokens=raw["usage"]["input_tokens"],
                output_tokens=raw["usage"]["output_tokens"],
                latency_ms=raw["latency_ms"],
                access_date=fingerprint["access_date"],
            )
        )
    return tuple(records)


def _claims_to_violations(parsed: JudgeReport | None, session_id: str) -> tuple[Violation, ...]:
    if parsed is None:
        return ()
    return tuple(
        Violation(
            constraint_id=claim.constraint_id,
            ocel_event_ids=tuple(claim.event_ids),
            session_id=session_id,
            detail=claim.explanation,
        )
        for claim in parsed.violations
    )


def judge(
    *,
    model_id: str,
    sample_index: int,
    condition: Condition,
    session_id: str,
    payload: str,
    candidate_event_ids: tuple[str, ...],
    prompts: JudgePrompts,
    ruleset: RuleSet,
    cache_dir: Path,
    client: JudgeClient | None,
) -> JudgeRun:
    """Run one judging pass, reading from cache when the key matches.

    ``client=None`` is the pure replay mode: a cache miss raises instead of
    calling any provider (the evaluation replays without API access).
    """
    payload_hash = input_hash(payload)
    prompt_hash = prompt_sha256(prompts, ruleset)
    key = cache_key(model_id, sample_index, condition, payload_hash, prompt_hash)
    record_path = cache_dir / f"{key}.json"

    if record_path.is_file():
        raw = json.loads(record_path.read_text(encoding="utf-8"))
        parsed_payload = raw["response"]["parsed"]
        parsed = None if parsed_payload is None else JudgeReport.model_validate(parsed_payload)
        return JudgeRun(
            model_id=model_id,
            sample_index=sample_index,
            condition=condition,
            session_id=session_id,
            violations=_claims_to_violations(parsed, session_id),
            schema_failure=bool(raw["response"]["schema_failure"]),
            cache_key=key,
            from_cache=True,
        )

    if client is None:
        raise JudgeError(
            f"cache miss for {key} and no client provided; the replay path "
            "requires a populated cache"
        )

    system_text, user_text = render_prompt(
        prompts, ruleset, condition=condition, session_id=session_id, payload=payload
    )
    request = JudgeRequest(
        model_id=model_id,
        sample_index=sample_index,
        condition=condition,
        session_id=session_id,
        system_text=system_text,
        user_text=user_text,
        schema=judge_schema(),
        candidate_event_ids=candidate_event_ids,
    )
    response = client.complete(request)
    parsed, schema_failure = _parse_report(response.text)
    _write_cache_record(
        record_path,
        request=request,
        response=response,
        payload_hash=payload_hash,
        prompt_hash=prompt_hash,
        prompt_version=prompts.prompt_version,
        parsed=parsed,
        schema_failure=schema_failure,
    )
    return JudgeRun(
        model_id=model_id,
        sample_index=sample_index,
        condition=condition,
        session_id=session_id,
        violations=_claims_to_violations(parsed, session_id),
        schema_failure=schema_failure,
        cache_key=key,
        from_cache=False,
    )


def _read_manifest_run_id(split_dir: Path) -> str:
    manifest = json.loads((split_dir / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest["run_id"])


def judge_all(
    split_dir: Path,
    log: OCELLog,
    *,
    models: Sequence[str] = JUDGE_MODELS,
    samples: int = 5,
    conditions: Sequence[Condition] = CONDITIONS,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    prompts_path: Path = DEFAULT_PROMPTS_PATH,
    rules_path: Path = Path("rules") / "rules.yaml",
    provider: Literal["anthropic", "scripted", "replay"] = "scripted",
    script_fn: Callable[[JudgeRequest], str] | None = None,
    allow_evaluation: bool = False,
) -> tuple[JudgeRun, ...]:
    """Judge every session of a split across models x samples x conditions.

    The evaluation lock fires BEFORE any judging: a split whose manifest
    ``run_id`` equals :data:`EVALUATION_BASE_RUN_ID` refuses to run unless
    ``allow_evaluation=True`` — which is legitimate only after the
    ``catalogue-v1`` freeze exists (I4).
    """
    run_id = _read_manifest_run_id(split_dir)
    if run_id == EVALUATION_BASE_RUN_ID and not allow_evaluation:
        raise EvaluationLockError(
            f"split {split_dir} belongs to the evaluation base run {run_id}; "
            "judging it is locked until the catalogue-v1 freeze exists "
            "(pass allow_evaluation=True only post-freeze)"
        )

    prompts = load_judge_prompts(prompts_path)
    ruleset = load_ruleset(rules_path)
    client: JudgeClient | None
    if provider == "anthropic":
        client = AnthropicJudgeClient()
    elif provider == "scripted":
        client = ScriptedJudgeClient(script_fn) if script_fn else ScriptedJudgeClient()
    elif provider == "replay":
        client = None
    else:  # pragma: no cover - Literal narrows this away for typed callers
        raise JudgeError(f"unknown provider {provider!r}")

    index = build_index(log)
    session_ids = sorted(
        {obj.object_id for obj in log.objects if obj.object_type.value == "Session"}
    )
    runs: list[JudgeRun] = []
    for session_id in session_ids:
        session_events = tuple(
            event.event_id
            for event in index.events
            if index.session_of_event.get(event.event_id) == session_id
        )
        for condition in conditions:
            payload = (
                spans_payload(split_dir, session_id)
                if condition == "spans"
                else session_excerpt(log, session_id)
            )
            for model_id in models:
                for sample_index in range(samples):
                    runs.append(
                        judge(
                            model_id=model_id,
                            sample_index=sample_index,
                            condition=condition,
                            session_id=session_id,
                            payload=payload,
                            candidate_event_ids=session_events,
                            prompts=prompts,
                            ruleset=ruleset,
                            cache_dir=cache_dir,
                            client=client,
                        )
                    )
    return tuple(runs)


# ---------------------------------------------------------------------------
# Aggregators — modal set vs per-item majority (both, per the spec's phrasings)
# ---------------------------------------------------------------------------

_SCHEMA_FAILURE_KEY: Final = '"SCHEMA_FAILURE"'


def _run_key(run: JudgeRun) -> str:
    # A schema failure is NOT an empty verdict: it forms its own modal class.
    if run.schema_failure:
        return _SCHEMA_FAILURE_KEY
    return verdict_set_key(run.violations)


def modal_verdict(runs: list[JudgeRun]) -> tuple[Violation, ...]:
    """The most frequently produced violation SET across runs.

    Whole-set agreement (what ``determinism_score`` measures): returns the
    violations of the lowest-sample-index run holding the modal key; ties
    break lexicographically on the key; a modal schema failure returns ().
    Distinct from :func:`majority_vote`, which is per-item.
    """
    if not runs:
        raise JudgeError("modal_verdict needs at least one run")
    counts: dict[str, int] = {}
    for run in runs:
        key = _run_key(run)
        counts[key] = counts.get(key, 0) + 1
    best = max(counts.values())
    modal_key = sorted(key for key, count in counts.items() if count == best)[0]
    if modal_key == _SCHEMA_FAILURE_KEY:
        return ()
    for run in sorted(runs, key=lambda r: (r.sample_index, r.model_id, r.condition)):
        if _run_key(run) == modal_key:
            return run.violations
    raise JudgeError("unreachable: modal key vanished")  # pragma: no cover


def majority_vote(
    runs: list[JudgeRun], *, k: int = 3, matching: JudgeMatching | None = None
) -> tuple[Violation, ...]:
    """The strong aggregated judge: per-ITEM majority across runs.

    A violation is kept iff its equivalence key — (normalised constraint id,
    frozenset of event ids), normalised through the synonym map when
    ``matching`` is given — appears in at least ``k`` of the runs. The
    representative is the earliest (sample, model, condition) occurrence;
    output in canonical violation order. Distinct from :func:`modal_verdict`
    (whole-set agreement).
    """

    def item_key(violation: Violation) -> tuple[str, frozenset[str]]:
        label = violation.constraint_id.strip().lower()
        if matching is not None:
            label = matching.synonyms.get(label, label)
        return (label.lower(), frozenset(violation.ocel_event_ids))

    support: dict[tuple[str, frozenset[str]], int] = {}
    representative: dict[tuple[str, frozenset[str]], Violation] = {}
    for run in sorted(runs, key=lambda r: (r.sample_index, r.model_id, r.condition)):
        seen_in_run: set[tuple[str, frozenset[str]]] = set()
        for violation in run.violations:
            key = item_key(violation)
            if key in seen_in_run:
                continue
            seen_in_run.add(key)
            support[key] = support.get(key, 0) + 1
            representative.setdefault(key, violation)
    kept = [violation for key, violation in representative.items() if support[key] >= k]
    return tuple(sorted(kept, key=violation_sort_key))

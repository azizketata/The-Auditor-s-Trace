"""The declarative scenario definition — the re-skinning seam.

Loads ``data/catalogue/scenario_credit.yaml`` into frozen dataclasses. A second
scenario is a second YAML plus (optionally) different node builders; the Phase 3
mapper never sees any of it. Uses ``pyyaml`` only, which is a core dependency.

Validation here is deliberately strict: the catalogue is what Phase 4's
violation injectors mutate and what Phase 5's ``rules.yaml`` derives its
parameters from, so an incoherent catalogue must fail at load time, not three
phases later.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml


class CatalogueError(ValueError):
    """The scenario catalogue is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class DataResourceSpec:
    """One data resource: what a ``retrieve_data`` event reads."""

    resource_id: str
    resource_type: str
    classification: str
    lawful_basis: str
    controller: str
    retention_class: str
    retrieved_by: str
    purpose: str
    required: bool


@dataclass(frozen=True, slots=True)
class PolicyVersionSpec:
    """One policy version; exactly one per catalogue is current."""

    policy_id: str
    version: str
    effective_from: str
    effective_to: str
    current: bool

    @property
    def object_id(self) -> str:
        """The OCEL object id for this policy version."""
        return f"{self.policy_id}-{self.version}"


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One agent of the fleet."""

    agent_id: str
    name: str
    role: str
    version: str
    framework: str
    entrypoint: bool
    prompt_name: str
    prompt_version: str
    prompt_template: str
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool an agent may call."""

    tool_name: str
    tool_type: str
    description: str


@dataclass(frozen=True, slots=True)
class ApproverSpec:
    """One human approver role with an amount-based authority ceiling."""

    approver_id: str
    role: str
    authority_level: int
    max_amount_dm: int


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """The LLM the fleet calls, including the recorded sampling provenance."""

    model_id: str
    provider: str
    version: str
    temperature: float
    seed: int
    max_tokens: int


@dataclass(frozen=True, slots=True)
class ScenarioCatalogue:
    """The full declarative scenario."""

    scenario_name: str
    scenario_version: str
    workflow_name: str
    agents: tuple[AgentSpec, ...]
    tools: tuple[ToolSpec, ...]
    resources: tuple[DataResourceSpec, ...]
    policies: tuple[PolicyVersionSpec, ...]
    approvers: tuple[ApproverSpec, ...]
    model: ModelSpec
    approval_required_outcomes: tuple[str, ...]
    adverse_outcomes: tuple[str, ...]
    required_source_types: tuple[str, ...]


def default_catalogue_path() -> Path:
    """The committed credit-scoring catalogue, relative to the repo root."""
    return Path("data") / "catalogue" / "scenario_credit.yaml"


def catalogue_digest(path: Path) -> str:
    """sha256 of the catalogue file, recorded in every run manifest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_catalogue(path: Path) -> ScenarioCatalogue:
    """Load and validate a scenario catalogue. Raises :class:`CatalogueError`."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CatalogueError(f"{path}: top level is not a mapping")
    try:
        catalogue = ScenarioCatalogue(
            scenario_name=str(raw["scenario_name"]),
            scenario_version=str(raw["scenario_version"]),
            workflow_name=str(raw["workflow_name"]),
            agents=tuple(
                AgentSpec(
                    agent_id=str(a["agent_id"]),
                    name=str(a["name"]),
                    role=str(a["role"]),
                    version=str(a["version"]),
                    framework=str(a["framework"]),
                    entrypoint=bool(a["entrypoint"]),
                    prompt_name=str(a["prompt_name"]),
                    prompt_version=str(a["prompt_version"]),
                    prompt_template=str(a["prompt_template"]),
                    tools=tuple(str(t) for t in a["tools"]),
                )
                for a in raw["agents"]
            ),
            tools=tuple(
                ToolSpec(
                    tool_name=str(t["tool_name"]),
                    tool_type=str(t["tool_type"]),
                    description=str(t["description"]),
                )
                for t in raw["tools"]
            ),
            resources=tuple(
                DataResourceSpec(
                    resource_id=str(r["resource_id"]),
                    resource_type=str(r["resource_type"]),
                    classification=str(r["classification"]),
                    lawful_basis=str(r["lawful_basis"]),
                    controller=str(r["controller"]),
                    retention_class=str(r["retention_class"]),
                    retrieved_by=str(r["retrieved_by"]),
                    purpose=str(r["purpose"]),
                    required=bool(r["required"]),
                )
                for r in raw["resources"]
            ),
            policies=tuple(
                PolicyVersionSpec(
                    policy_id=str(p["policy_id"]),
                    version=str(p["version"]),
                    effective_from=str(p["effective_from"]),
                    effective_to=str(p["effective_to"]),
                    current=bool(p["current"]),
                )
                for p in raw["policies"]
            ),
            approvers=tuple(
                ApproverSpec(
                    approver_id=str(x["approver_id"]),
                    role=str(x["role"]),
                    authority_level=int(x["authority_level"]),
                    max_amount_dm=int(x["max_amount_dm"]),
                )
                for x in raw["approvers"]
            ),
            model=ModelSpec(
                model_id=str(raw["model"]["model_id"]),
                provider=str(raw["model"]["provider"]),
                version=str(raw["model"]["version"]),
                temperature=float(raw["model"]["temperature"]),
                seed=int(raw["model"]["seed"]),
                max_tokens=int(raw["model"]["max_tokens"]),
            ),
            approval_required_outcomes=tuple(str(o) for o in raw["approval_required_outcomes"]),
            adverse_outcomes=tuple(str(o) for o in raw["adverse_outcomes"]),
            required_source_types=tuple(str(s) for s in raw["required_source_types"]),
        )
    except (KeyError, TypeError) as exc:
        raise CatalogueError(f"{path}: {exc!r}") from exc
    _validate(catalogue, path)
    return catalogue


def _validate(c: ScenarioCatalogue, path: Path) -> None:
    def unique(name: str, ids: list[str]) -> None:
        if len(ids) != len(set(ids)):
            raise CatalogueError(f"{path}: duplicate {name}: {sorted(ids)}")

    unique("agent ids", [a.agent_id for a in c.agents])
    unique("agent roles", [a.role for a in c.agents])
    unique("tool names", [t.tool_name for t in c.tools])
    unique("resource ids", [r.resource_id for r in c.resources])
    unique("policy object ids", [p.object_id for p in c.policies])
    unique("approver ids", [a.approver_id for a in c.approvers])

    entrypoints = [a for a in c.agents if a.entrypoint]
    if len(entrypoints) != 1:
        raise CatalogueError(f"{path}: exactly one entrypoint agent required, got {entrypoints}")

    current = [p for p in c.policies if p.current]
    if len(current) != 1:
        raise CatalogueError(f"{path}: exactly one current policy required")
    if current[0].effective_to != "":
        raise CatalogueError(f"{path}: the current policy must have an open effective_to")
    if not any(not p.current for p in c.policies):
        raise CatalogueError(f"{path}: at least one superseded policy required (V8 raw material)")
    for p in c.policies:
        if not p.current and p.effective_to == "":
            raise CatalogueError(f"{path}: superseded policy {p.object_id} has open effective_to")

    tool_names = {t.tool_name for t in c.tools}
    for a in c.agents:
        missing = set(a.tools) - tool_names
        if missing:
            raise CatalogueError(f"{path}: agent {a.agent_id} references unknown tools {missing}")
    for r in c.resources:
        if r.retrieved_by not in tool_names:
            raise CatalogueError(
                f"{path}: resource {r.resource_id} retrieved_by unknown tool {r.retrieved_by!r}"
            )

    resource_types = {r.resource_type for r in c.resources if r.required}
    missing_required = set(c.required_source_types) - resource_types
    if missing_required:
        raise CatalogueError(
            f"{path}: required_source_types {sorted(missing_required)} have no required resource"
        )

    special = [r for r in c.resources if r.classification == "special_category"]
    if not special:
        raise CatalogueError(f"{path}: no special_category resource (V5 raw material)")
    for r in special:
        if not r.lawful_basis.strip():
            raise CatalogueError(
                f"{path}: special_category resource {r.resource_id} must carry a "
                "non-empty lawful_basis in the clean catalogue (V5 blanks it at injection)"
            )

    if "refer" in c.approval_required_outcomes:
        raise CatalogueError(
            f"{path}: 'refer' must not require approval — it is the escalation outcome, and "
            "including it makes every human-override session a T1 false positive on the clean split"
        )
    if not c.approvers:
        raise CatalogueError(f"{path}: at least one approver required")


def current_policy(c: ScenarioCatalogue) -> PolicyVersionSpec:
    """The one policy version in force."""
    return next(p for p in c.policies if p.current)


def superseded_policies(c: ScenarioCatalogue) -> tuple[PolicyVersionSpec, ...]:
    """Every policy version no longer in force, declared for V8's injector."""
    return tuple(p for p in c.policies if not p.current)


def agent_by_role(c: ScenarioCatalogue, role: str) -> AgentSpec:
    """Look an agent up by its role name."""
    for a in c.agents:
        if a.role == role:
            return a
    raise CatalogueError(f"no agent with role {role!r}")


def resource_by_tool(c: ScenarioCatalogue, tool_name: str) -> DataResourceSpec | None:
    """The resource a tool retrieves, or ``None`` for non-retrieval tools."""
    for r in c.resources:
        if r.retrieved_by == tool_name:
            return r
    return None


def route_approver(c: ScenarioCatalogue, amount_dm: int) -> ApproverSpec:
    """Route an application to the least-senior approver whose ceiling covers it."""
    for approver in sorted(c.approvers, key=lambda a: a.authority_level):
        if amount_dm <= approver.max_amount_dm:
            return approver
    raise CatalogueError(f"no approver can authorise {amount_dm} DM")


def prompt_template_hash(a: AgentSpec) -> str:
    """sha256 of the prompt template — the Prompt object's template_hash."""
    return hashlib.sha256(a.prompt_template.encode("utf-8")).hexdigest()[:16]


def scripted_model_spec(base: ModelSpec) -> ModelSpec:
    """The honestly-named scripted stand-in for the catalogue's model.

    A scripted span must never impersonate a vendor model id in a published
    audit dataset, so the swap renames the model rather than only rerouting it.
    """
    return ModelSpec(
        model_id="scripted-credit-policy-v1",
        provider="scripted",
        version="1.0.0",
        temperature=base.temperature,
        seed=base.seed,
        max_tokens=base.max_tokens,
    )

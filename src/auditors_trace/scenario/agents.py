"""The four-agent credit workflow plus a simulated human approval step.

Agents: intake, data_retrieval, scoring, adjudication — a real LangGraph
``StateGraph``, instrumented by OpenInference, emitting the ``at.*`` governance
contract through ``scenario.emit``. Each session produces 28 events covering
all 13 OCEL event types and all 12 object types.

Handoffs are explicit ``Command(goto=...)`` returns (LangGraph 1.2.11 has no
handoff helper) and are emitted by the *source* node just before it returns,
so the handoff span is a sibling of the source's ``invoke_agent`` span, never
nested under the target.

The human approval step is a plain scripted gateway
(:func:`auditors_trace.scenario.policy.approval_decision`), deliberately not
``langgraph.types.interrupt()`` — ``interrupt`` re-executes the whole node body
on resume, which would emit every prior span twice.

LLM output is not bitwise reproducible with a real provider, and that is
expected: determinism is required of the analysis pipeline, not the subject
under test. With ``provider="scripted"`` the whole run is byte-reproducible,
which is what makes the committed span fixtures golden.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import (
    SPAN_CONTRACT_VERSION,
    AttrValue,
    O2ORef,
    ObjectRef,
    SessionScope,
)
from auditors_trace.scenario.catalogue import (
    AgentSpec,
    ModelSpec,
    PolicyVersionSpec,
    ScenarioCatalogue,
    agent_by_role,
    catalogue_digest,
    current_policy,
    default_catalogue_path,
    load_catalogue,
    prompt_template_hash,
    resource_by_tool,
    route_approver,
    scripted_model_spec,
    superseded_policies,
)
from auditors_trace.scenario.clock import (
    SESSION_STRIDE_SECONDS,
    assert_within_policy,
    session_clock,
)
from auditors_trace.scenario.emit import (
    SessionContext,
    check_declarations,
    event_span,
    langchain_config,
    session_span,
)
from auditors_trace.scenario.models import build_chat_model
from auditors_trace.scenario.policy import (
    Assessment,
    approval_decision,
    assess,
    final_decision_statement,
    final_outcome,
    final_reason_codes,
    rng_key,
    scripted_message,
)
from auditors_trace.scenario.seed import (
    Applicant,
    application_id,
    download_seed,
    load_applicants,
    seed_digest,
)
from auditors_trace.scenario.telemetry import (
    SPAN_FILE_SCHEMA_VERSION,
    RunManifest,
    Telemetry,
    compute_run_id,
    telemetry,
    write_manifest,
)


class FleetState(TypedDict, total=False):
    """The LangGraph state passed between agents."""

    messages: Annotated[list[BaseMessage], add_messages]
    facts: dict[str, str]
    assessment: Assessment
    outcome: str
    approval_granted: bool


@dataclass(frozen=True, slots=True)
class SessionRuntime:
    """Everything one session's node closures need."""

    ctx: SessionContext
    applicant: Applicant
    catalogue: ScenarioCatalogue
    tools: Mapping[str, BaseTool]
    policy: PolicyVersionSpec
    model_spec: ModelSpec
    seed: int
    application_id: str


class NodeFn(Protocol):
    """A graph node: LangGraph's node protocol names the parameter ``state``."""

    def __call__(self, state: FleetState) -> Command[Any]: ...


NodeBuilder = Callable[[SessionRuntime, AgentSpec], NodeFn]


@dataclass(frozen=True, slots=True)
class FleetConfig:
    """The declarative fleet: catalogue + graph shape + node builders.

    Re-skinning seam: a second scenario supplies its own catalogue YAML and
    builders; nothing downstream of the emitted spans changes.
    """

    catalogue: ScenarioCatalogue
    node_order: tuple[str, ...]
    handoffs: tuple[tuple[str, str], ...]
    entrypoint: str
    builders: Mapping[str, NodeBuilder]


@dataclass(frozen=True, slots=True)
class SessionResult:
    """The summary of one completed session."""

    session_id: str
    application_id: str
    applicant_id: str
    event_count: int
    outcome: str
    approval_granted: bool


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _session_ref(rt: SessionRuntime) -> ObjectRef:
    return ObjectRef(ObjectType.SESSION, rt.ctx.scope.session_id, Qualifier.WITHIN)


def _application_ref(rt: SessionRuntime) -> ObjectRef:
    return ObjectRef(ObjectType.APPLICATION, rt.application_id, Qualifier.CONCERNS)


def _agent_performs(rt: SessionRuntime, spec: AgentSpec) -> ObjectRef:
    return ObjectRef(ObjectType.AGENT, spec.agent_id, Qualifier.PERFORMED_BY)


def _maybe_declare(rt: SessionRuntime, ref: ObjectRef) -> ObjectRef:
    """Turn a reference into a declaration iff the object is not yet declared."""
    if ref.object_id in rt.ctx.declared or not ref.attributes:
        return ObjectRef(ref.object_type, ref.object_id, ref.qualifier)
    return ObjectRef(
        ref.object_type, ref.object_id, ref.qualifier, declares=True, attributes=ref.attributes
    )


def _emit_invoke_agent(rt: SessionRuntime, spec: AgentSpec, step_index: int) -> None:
    with event_span(
        rt.ctx,
        event_type=EventType.INVOKE_AGENT,
        name=f"invoke_agent {spec.role}",
        actor=(ObjectType.AGENT, spec.agent_id),
        attributes={"agent_role": spec.role, "step_index": step_index},
        objects=[
            _maybe_declare(
                rt,
                ObjectRef(
                    ObjectType.AGENT,
                    spec.agent_id,
                    Qualifier.PERFORMED_BY,
                    attributes=(
                        ("agent_id", spec.agent_id),
                        ("name", spec.name),
                        ("role", spec.role),
                        ("version", spec.version),
                        ("framework", spec.framework),
                    ),
                ),
            ),
            _session_ref(rt),
            _application_ref(rt),
        ],
        span_kind="AGENT",
    ):
        pass


def _emit_call_llm(rt: SessionRuntime, spec: AgentSpec, purpose: str, script: str) -> str:
    """Emit the call_llm event and run the real chat model inside it.

    Returns the assistant text (the script for the scripted provider; the
    genuine completion for a real one).
    """
    m = rt.model_spec
    prompt_id = f"PRM-{spec.prompt_name}-v{spec.prompt_version}"
    with event_span(
        rt.ctx,
        event_type=EventType.CALL_LLM,
        name=f"call_llm {spec.role}",
        actor=(ObjectType.AGENT, spec.agent_id),
        attributes={
            "temperature": m.temperature,
            "model_seed": m.seed,
            "max_tokens": m.max_tokens,
            "purpose": purpose,
        },
        objects=[
            _maybe_declare(
                rt,
                ObjectRef(
                    ObjectType.MODEL,
                    m.model_id,
                    Qualifier.USES,
                    attributes=(
                        ("model_id", m.model_id),
                        ("provider", m.provider),
                        ("version", m.version),
                    ),
                ),
            ),
            _maybe_declare(
                rt,
                ObjectRef(
                    ObjectType.PROMPT,
                    prompt_id,
                    Qualifier.USES,
                    attributes=(
                        ("prompt_name", spec.prompt_name),
                        ("prompt_version", spec.prompt_version),
                        ("template_hash", prompt_template_hash(spec)),
                    ),
                ),
            ),
            _agent_performs(rt, spec),
            _application_ref(rt),
            _session_ref(rt),
        ],
        span_kind="CHAIN",
    ):
        model = build_chat_model(m, script)
        messages = [
            SystemMessage(content=spec.prompt_template),
            HumanMessage(
                content=(
                    f"Application {rt.application_id} for applicant "
                    f"{rt.applicant.applicant_id}: {rt.applicant.credit_amount_dm} DM over "
                    f"{rt.applicant.duration_months} months, purpose {rt.applicant.purpose}."
                )
            ),
        ]
        response = model.invoke(
            messages, config=langchain_config(rt.ctx, f"{spec.role} llm", spec.role)
        )
        return str(response.content)


def _emit_call_tool(
    rt: SessionRuntime, spec: AgentSpec, tool_name: str, args: dict[str, Any]
) -> str:
    tool_spec = next(t for t in rt.catalogue.tools if t.tool_name == tool_name)
    with event_span(
        rt.ctx,
        event_type=EventType.CALL_TOOL,
        name=f"call_tool {tool_name}",
        actor=(ObjectType.AGENT, spec.agent_id),
        attributes={
            "tool_call_id": f"TC-{rt.ctx.scope.session_id}-{tool_name}",
            "status": "ok",
        },
        objects=[
            _maybe_declare(
                rt,
                ObjectRef(
                    ObjectType.TOOL,
                    f"TOOL-{tool_name}",
                    Qualifier.USES,
                    attributes=(("tool_name", tool_name), ("tool_type", tool_spec.tool_type)),
                ),
            ),
            _agent_performs(rt, spec),
            _application_ref(rt),
            _session_ref(rt),
        ],
        span_kind="CHAIN",
    ):
        output = rt.tools[tool_name].invoke(
            args, config=langchain_config(rt.ctx, f"tool {tool_name}", spec.role)
        )
        return str(output)


def _emit_retrieve_data(rt: SessionRuntime, spec: AgentSpec, tool_name: str) -> None:
    resource = resource_by_tool(rt.catalogue, tool_name)
    assert resource is not None, f"tool {tool_name} retrieves no resource"
    objects = [
        _maybe_declare(
            rt,
            ObjectRef(
                ObjectType.DATA_RESOURCE,
                resource.resource_id,
                Qualifier.READS,
                attributes=(
                    ("resource_id", resource.resource_id),
                    ("resource_type", resource.resource_type),
                    ("classification", resource.classification),
                    ("lawful_basis", resource.lawful_basis),
                    ("controller", resource.controller),
                    ("retention_class", resource.retention_class),
                ),
            ),
        ),
        _application_ref(rt),
        _agent_performs(rt, spec),
        _session_ref(rt),
    ]
    if resource.classification == "special_category":
        objects.append(
            ObjectRef(ObjectType.APPLICANT, rt.applicant.applicant_id, Qualifier.CONCERNS)
        )
    with event_span(
        rt.ctx,
        event_type=EventType.RETRIEVE_DATA,
        name=f"retrieve_data {resource.resource_type}",
        actor=(ObjectType.AGENT, spec.agent_id),
        attributes={
            "purpose": resource.purpose,
            "resource_type": resource.resource_type,
            "record_count": 1,
        },
        objects=objects,
        span_kind="RETRIEVER",
    ):
        pass


def _emit_reasoning(
    rt: SessionRuntime,
    spec: AgentSpec,
    kind: str,
    text: str,
    *,
    decision_id: str | None = None,
    reason_codes: tuple[str, ...] = (),
) -> None:
    objects = [_agent_performs(rt, spec), _application_ref(rt), _session_ref(rt)]
    if decision_id is not None:
        objects.append(ObjectRef(ObjectType.CREDIT_DECISION, decision_id, Qualifier.EXPLAINS))
    attributes: dict[str, AttrValue] = {"reasoning_kind": kind, "text_hash": _text_hash(text)}
    if reason_codes:
        attributes["reason_codes"] = reason_codes
    with event_span(
        rt.ctx,
        event_type=EventType.EMIT_REASONING,
        name=f"emit_reasoning {kind}",
        actor=(ObjectType.AGENT, spec.agent_id),
        attributes=attributes,
        objects=objects,
    ):
        pass


def _emit_handoff(rt: SessionRuntime, source: AgentSpec, target: AgentSpec) -> None:
    with event_span(
        rt.ctx,
        event_type=EventType.HANDOFF,
        name=f"handoff {source.role}->{target.role}",
        actor=(ObjectType.AGENT, source.agent_id),
        attributes={"reason": f"{source.role} complete", "handoff_kind": "sequential"},
        objects=[
            ObjectRef(ObjectType.AGENT, source.agent_id, Qualifier.FROM),
            ObjectRef(ObjectType.AGENT, target.agent_id, Qualifier.TO),
            _session_ref(rt),
            _application_ref(rt),
        ],
        o2o=[O2ORef(source.agent_id, target.agent_id, Qualifier.DELEGATES_TO)],
    ):
        pass


def _build_intake(rt: SessionRuntime, spec: AgentSpec) -> NodeFn:
    target = agent_by_role(rt.catalogue, "data_retrieval")

    def intake(state: FleetState) -> Command[Literal["data_retrieval"]]:
        _emit_invoke_agent(rt, spec, step_index=1)
        text = _emit_call_llm(
            rt, spec, "intake_summary", scripted_message("intake", rt.applicant, None, {})
        )
        _emit_reasoning(rt, spec, "intake_summary", text)
        _emit_handoff(rt, spec, target)
        return Command(
            update={"facts": {"intake_summary_hash": _text_hash(text)}},
            goto="data_retrieval",
        )

    return intake


_RETRIEVAL_ORDER = (
    "credit_bureau_lookup",
    "income_verification",
    "internal_credit_history",
    "demographic_bias_monitor",
)


def _build_data_retrieval(rt: SessionRuntime, spec: AgentSpec) -> NodeFn:
    target = agent_by_role(rt.catalogue, "scoring")

    def data_retrieval(state: FleetState) -> Command[Literal["scoring"]]:
        _emit_invoke_agent(rt, spec, step_index=2)
        for tool_name in _RETRIEVAL_ORDER:
            args: dict[str, Any] = (
                {"applicant_ref": rt.applicant.applicant_id}
                if tool_name == "internal_credit_history"
                else {"application_id": rt.application_id}
            )
            _emit_call_tool(rt, spec, tool_name, args)
            _emit_retrieve_data(rt, spec, tool_name)
        _emit_handoff(rt, spec, target)
        sources = ", ".join(
            r.resource_type for t in _RETRIEVAL_ORDER if (r := resource_by_tool(rt.catalogue, t))
        )
        facts = dict(state.get("facts", {}))
        facts["sources"] = sources
        return Command(update={"facts": facts}, goto="scoring")

    return data_retrieval


def _build_scoring(rt: SessionRuntime, spec: AgentSpec) -> NodeFn:
    target = agent_by_role(rt.catalogue, "adjudication")

    def scoring(state: FleetState) -> Command[Literal["adjudication"]]:
        _emit_invoke_agent(rt, spec, step_index=3)
        _emit_call_tool(rt, spec, "risk_scorecard", {"features_json": "{}"})
        verdict = assess(rt.applicant, rt.policy)
        text = _emit_call_llm(
            rt, spec, "score_rationale", scripted_message("scoring", rt.applicant, verdict, {})
        )
        adverse = verdict.outcome in rt.catalogue.adverse_outcomes
        _emit_reasoning(
            rt,
            spec,
            "score_rationale",
            text,
            reason_codes=verdict.reason_codes if adverse else (),
        )
        _emit_handoff(rt, spec, target)
        return Command(update={"assessment": verdict}, goto="adjudication")

    return scoring


def _build_adjudication(rt: SessionRuntime, spec: AgentSpec) -> NodeFn:
    def adjudication(state: FleetState) -> Command[Literal["__end__"]]:
        _emit_invoke_agent(rt, spec, step_index=4)
        verdict = state["assessment"]
        facts = state.get("facts", {})
        amount = rt.applicant.credit_amount_dm
        decision_id = f"DEC-{rt.application_id}"
        approval_id = f"APRV-{rt.ctx.scope.session_id}"

        # The pre-approval draft notice: kept as telemetry on the call_llm
        # span's layer-B child. The post-decision emit_reasoning hashes the
        # final statement instead — under an override the two legitimately
        # differ, and hashing the draft would commit the Art. 13/86 artefact
        # to a statement naming a different outcome.
        _emit_call_llm(
            rt,
            spec,
            "decision_notice",
            scripted_message("adjudication", rt.applicant, verdict, facts),
        )

        approver = route_approver(rt.catalogue, amount)
        approval = approval_decision(
            verdict, amount, approver, rng_key(rt.seed, rt.application_id, "approval")
        )

        with event_span(
            rt.ctx,
            event_type=EventType.REQUEST_APPROVAL,
            name="request_approval",
            actor=(ObjectType.AGENT, spec.agent_id),
            attributes={
                "proposed_outcome": verdict.outcome,
                "proposed_score": verdict.score,
                "amount_dm": amount,
                "required_role": approver.role,
                "sla_seconds": 86400,
            },
            objects=[
                ObjectRef(ObjectType.APPROVAL, approval_id, Qualifier.REQUESTS),
                ObjectRef(
                    ObjectType.HUMAN_APPROVER,
                    approver.approver_id,
                    Qualifier.CONCERNS,
                    declares=True,
                    attributes=(
                        ("approver_id", approver.approver_id),
                        ("role", approver.role),
                        ("authority_level", approver.authority_level),
                    ),
                ),
                ObjectRef(ObjectType.CREDIT_DECISION, decision_id, Qualifier.CONCERNS),
                _application_ref(rt),
                _agent_performs(rt, spec),
                _session_ref(rt),
            ],
        ):
            pass

        approval_event = EventType.GRANT_APPROVAL if approval.granted else EventType.DENY_APPROVAL
        decided_at = rt.ctx.clock.peek(approval_event)
        approval_attrs: dict[str, AttrValue] = {
            "approver_role": approval.approver_role,
            "authority_level": approval.authority_level,
            "rationale_hash": _text_hash(approval.rationale),
        }
        if not approval.granted:
            approval_attrs["override_reason"] = "manual_review_required"
        with event_span(
            rt.ctx,
            event_type=approval_event,
            name=approval_event.value,
            actor=(ObjectType.HUMAN_APPROVER, approver.approver_id),
            attributes=approval_attrs,
            objects=[
                ObjectRef(
                    ObjectType.APPROVAL,
                    approval_id,
                    Qualifier.PRODUCES,
                    declares=True,
                    attributes=(
                        ("approval_id", approval_id),
                        ("approver_role", approval.approver_role),
                        ("granted", approval.granted),
                        ("decided_at", decided_at),
                    ),
                ),
                ObjectRef(ObjectType.HUMAN_APPROVER, approver.approver_id, Qualifier.PERFORMED_BY),
                # Section 5 reserves `approves` for grant_approval; a denial
                # event asserting it approves the decision would be inverted.
                ObjectRef(
                    ObjectType.CREDIT_DECISION,
                    decision_id,
                    Qualifier.APPROVES if approval.granted else Qualifier.CONCERNS,
                ),
                _application_ref(rt),
                _session_ref(rt),
            ],
        ):
            pass

        outcome = final_outcome(verdict, approval)
        adverse = outcome in rt.catalogue.adverse_outcomes
        # Codes reflect the FINAL outcome: an override adds RC98, the true
        # principal reason. assess() guarantees non-empty codes for its own
        # adverse verdicts, so adverse decisions always carry codes.
        reason_codes = final_reason_codes(verdict, approval)
        decision_time = rt.ctx.clock.peek(EventType.MAKE_DECISION)
        with event_span(
            rt.ctx,
            event_type=EventType.MAKE_DECISION,
            name="make_decision",
            actor=(ObjectType.AGENT, spec.agent_id),
            attributes={
                "outcome": outcome,
                "score": verdict.score,
                "reason_codes": reason_codes if adverse else (),
                "adverse": adverse,
            },
            objects=[
                ObjectRef(
                    ObjectType.CREDIT_DECISION,
                    decision_id,
                    Qualifier.PRODUCES,
                    declares=True,
                    attributes=(
                        ("decision_id", decision_id),
                        ("outcome", outcome),
                        ("score", verdict.score),
                        ("reason_codes", reason_codes if adverse else ()),
                        ("band", verdict.band),
                        ("decided_at", decision_time),
                    ),
                ),
                _application_ref(rt),
                ObjectRef(ObjectType.APPLICANT, rt.applicant.applicant_id, Qualifier.CONCERNS),
                ObjectRef(ObjectType.POLICY_VERSION, rt.policy.object_id, Qualifier.GOVERNED_BY),
                _agent_performs(rt, spec),
                ObjectRef(ObjectType.APPROVAL, approval_id, Qualifier.CONCERNS),
                _session_ref(rt),
            ],
            o2o=[O2ORef(decision_id, rt.application_id, Qualifier.DERIVED_FROM)],
        ):
            pass

        # The post-decision statement describes the decision as actually made.
        # `text` (the pre-approval LLM draft) legitimately differs in override
        # sessions — hashing it here would commit the Art. 13/86 artefact to a
        # statement naming a different outcome.
        _emit_reasoning(
            rt,
            spec,
            "adverse_action_statement",
            final_decision_statement(outcome, verdict.score, reason_codes if adverse else ()),
            decision_id=decision_id,
            reason_codes=reason_codes if adverse else (),
        )

        with event_span(
            rt.ctx,
            event_type=EventType.NOTIFY_APPLICANT,
            name="notify_applicant",
            actor=(ObjectType.AGENT, spec.agent_id),
            attributes={
                "channel": "letter",
                "adverse_action_notice": adverse,
                "contains_reason_codes": adverse,
            },
            objects=[
                ObjectRef(ObjectType.APPLICANT, rt.applicant.applicant_id, Qualifier.CONCERNS),
                ObjectRef(ObjectType.CREDIT_DECISION, decision_id, Qualifier.CONCERNS),
                _application_ref(rt),
                _agent_performs(rt, spec),
                _session_ref(rt),
            ],
        ):
            pass

        return Command(
            update={"outcome": outcome, "approval_granted": approval.granted},
            goto="__end__",
        )

    return adjudication


_DEFAULT_BUILDERS: Mapping[str, NodeBuilder] = {
    "intake": _build_intake,
    "data_retrieval": _build_data_retrieval,
    "scoring": _build_scoring,
    "adjudication": _build_adjudication,
}


def default_fleet(catalogue: ScenarioCatalogue | None = None) -> FleetConfig:
    """The credit-scoring fleet: intake, retrieval, scoring, adjudication."""
    cat = catalogue if catalogue is not None else load_catalogue(default_catalogue_path())
    return FleetConfig(
        catalogue=cat,
        node_order=("intake", "data_retrieval", "scoring", "adjudication"),
        handoffs=(
            ("intake", "data_retrieval"),
            ("data_retrieval", "scoring"),
            ("scoring", "adjudication"),
        ),
        entrypoint="intake",
        builders=_DEFAULT_BUILDERS,
    )


def build_graph(fleet: FleetConfig, runtime: SessionRuntime) -> Any:
    """Compile the LangGraph for one session's runtime."""
    destinations = dict(fleet.handoffs)
    builder: StateGraph[FleetState, None, FleetState, FleetState] = StateGraph(FleetState)
    for role in fleet.node_order:
        spec = agent_by_role(fleet.catalogue, role)
        dest = destinations.get(role)
        metadata: dict[str, Any] = {"at_agent_role": role}
        builder.add_node(
            role,
            fleet.builders[role](runtime, spec),
            metadata=metadata,
            destinations=(dest,) if dest else None,
        )
    builder.add_edge(START, fleet.entrypoint)
    return builder.compile(name=fleet.catalogue.workflow_name)


def _emit_session_start(rt: SessionRuntime, run_id: str, seed: int) -> None:
    cat = rt.catalogue
    a = rt.applicant
    policy_refs = [
        ObjectRef(
            ObjectType.POLICY_VERSION,
            rt.policy.object_id,
            Qualifier.GOVERNED_BY,
            declares=True,
            attributes=(
                ("policy_id", rt.policy.policy_id),
                ("version", rt.policy.version),
                ("effective_from", rt.policy.effective_from),
                ("effective_to", rt.policy.effective_to),
            ),
        )
    ]
    for old in superseded_policies(cat):
        policy_refs.append(
            ObjectRef(
                ObjectType.POLICY_VERSION,
                old.object_id,
                qualifier=None,  # pure declaration: present in the log, related to nothing
                declares=True,
                attributes=(
                    ("policy_id", old.policy_id),
                    ("version", old.version),
                    ("effective_from", old.effective_from),
                    ("effective_to", old.effective_to),
                ),
            )
        )
    with event_span(
        rt.ctx,
        event_type=EventType.SESSION_START,
        name="session_start",
        actor=None,  # deliberately no performed_by: T3 must not flag session bounds
        attributes={
            "scenario_name": cat.scenario_name,
            "scenario_version": cat.scenario_version,
            "contract": SPAN_CONTRACT_VERSION,
            "run_id": run_id,
            "seed": seed,
        },
        objects=[
            ObjectRef(
                ObjectType.SESSION,
                rt.ctx.scope.session_id,
                Qualifier.WITHIN,
                declares=True,
                attributes=(
                    ("session_id", rt.ctx.scope.session_id),
                    ("workflow_name", cat.workflow_name),
                ),
            ),
            ObjectRef(
                ObjectType.APPLICATION,
                rt.application_id,
                Qualifier.CONCERNS,
                declares=True,
                attributes=(
                    ("application_id", rt.application_id),
                    ("product_type", "consumer_credit"),
                    ("amount", a.credit_amount_dm),
                    ("currency", "DEM"),
                    ("term_months", a.duration_months),
                    ("purpose", a.purpose),
                    ("submitted_at", rt.ctx.clock.peek(EventType.SESSION_START)),
                ),
            ),
            ObjectRef(
                ObjectType.APPLICANT,
                a.applicant_id,
                Qualifier.CONCERNS,
                declares=True,
                attributes=(
                    ("applicant_id", a.applicant_id),
                    ("jurisdiction", "DE"),
                    ("pseudonym_scheme", "sha256/seeded"),
                ),
            ),
            *policy_refs,
        ],
        o2o=[O2ORef(rt.application_id, a.applicant_id, Qualifier.SUBMITTED_BY)],
    ):
        pass


def _emit_session_end(rt: SessionRuntime, outcome: str) -> None:
    decision_id = f"DEC-{rt.application_id}"
    with event_span(
        rt.ctx,
        event_type=EventType.SESSION_END,
        name="session_end",
        actor=None,
        attributes={
            "event_count": rt.ctx.seq + 1,  # including this event
            "outcome": outcome,
            "duration_seconds": rt.ctx.clock.elapsed_seconds(),
        },
        objects=[
            _session_ref(rt),
            _application_ref(rt),
            ObjectRef(ObjectType.CREDIT_DECISION, decision_id, Qualifier.CONCERNS),
        ],
    ):
        pass


def run_session(
    applicant: Applicant,
    fleet: FleetConfig,
    seed: int,
    *,
    telemetry: Telemetry,
    session_index: int,
    run_id: str,
    model_spec: ModelSpec,
) -> SessionResult:
    """Run one application end to end, emitting spans through the exporter."""
    catalogue = fleet.catalogue
    session_id = f"SESS-{session_index:04d}"
    telemetry.ids.begin_session(session_id)
    clock = session_clock(session_index)
    policy = current_policy(catalogue)
    assert_within_policy(clock, policy)
    app_id = application_id(applicant, seed)

    scope = SessionScope(
        scenario_name=catalogue.scenario_name,
        scenario_version=catalogue.scenario_version,
        run_id=run_id,
        run_seed=seed,
        session_id=session_id,
        workflow_name=catalogue.workflow_name,
        application_id=app_id,
        applicant_id=applicant.applicant_id,
    )
    ctx = SessionContext(scope=scope, tracer=telemetry.tracer, clock=clock)
    from auditors_trace.scenario.tools import build_tools

    runtime = SessionRuntime(
        ctx=ctx,
        applicant=applicant,
        catalogue=catalogue,
        tools=build_tools(catalogue, applicant, policy),
        policy=policy,
        model_spec=model_spec,
        seed=seed,
        application_id=app_id,
    )

    with session_span(telemetry.tracer, scope):
        _emit_session_start(runtime, run_id, seed)
        graph = build_graph(fleet, runtime)
        initial: FleetState = {"messages": [], "facts": {}, "outcome": ""}
        final = graph.invoke(
            initial, config=langchain_config(ctx, f"session {session_id}", "graph")
        )
        outcome = str(final["outcome"])
        _emit_session_end(runtime, outcome)
        check_declarations(ctx)
        if clock.elapsed_seconds() >= SESSION_STRIDE_SECONDS:
            # Overlapping simulated sessions would corrupt every temporal
            # predicate downstream (T1, V8). A re-skinned scenario with more
            # events must raise the stride, not silently overrun it.
            raise ValueError(
                f"{session_id}: simulated session footprint "
                f"{clock.elapsed_seconds()}s exceeds the {SESSION_STRIDE_SECONDS}s stride"
            )

    return SessionResult(
        session_id=session_id,
        application_id=app_id,
        applicant_id=applicant.applicant_id,
        event_count=ctx.event_count,
        outcome=outcome,
        approval_granted=bool(final.get("approval_granted", False)),
    )


def run_fleet(
    count: int,
    seed: int,
    out_dir: Path,
    fleet: FleetConfig | None = None,
    *,
    provider: str = "scripted",
    genai_semconv: bool = True,
    seed_data: Path | None = None,
    catalogue_path: Path | None = None,
    quiet: bool = False,
) -> Path:
    """Run ``count`` sessions and write spans + manifest. Returns ``out_dir``.

    Single-threaded by design: the exporter's deterministic ordering depends
    on it. Default provider is ``scripted`` so the run is hermetic; pass
    ``provider="anthropic"`` (and set ``ANTHROPIC_API_KEY``) for the artefact.
    """
    cat_path = catalogue_path if catalogue_path is not None else default_catalogue_path()
    resolved_fleet = fleet if fleet is not None else default_fleet(load_catalogue(cat_path))
    catalogue = resolved_fleet.catalogue

    if provider == "scripted":
        model_spec = scripted_model_spec(catalogue.model)
    elif provider == catalogue.model.provider:
        model_spec = catalogue.model
    else:
        raise ValueError(f"unknown provider {provider!r}; use 'scripted' or 'anthropic'")

    data_path = seed_data if seed_data is not None else download_seed(Path("data") / "seed")
    applicants = load_applicants(data_path, count, seed)
    catalogue_sha = catalogue_digest(cat_path)
    seed_sha = seed_digest(data_path)
    run_id = compute_run_id(
        seed, count, catalogue.scenario_name, catalogue.scenario_version, catalogue_sha, seed_sha
    )

    # A stale manifest from a previous run must never pair with this run's
    # files: the manifest is Phase 3's only entry point, so a partial run
    # leaves no valid one behind.
    stale_manifest = out_dir / "manifest.json"
    if stale_manifest.exists():
        stale_manifest.unlink()

    results: list[SessionResult] = []
    with telemetry(out_dir, run_id, genai_semconv=genai_semconv) as tel:
        for index, applicant in enumerate(applicants):
            result = run_session(
                applicant,
                resolved_fleet,
                seed,
                telemetry=tel,
                session_index=index,
                run_id=run_id,
                model_spec=model_spec,
            )
            results.append(result)
            if not quiet:
                print(
                    f"{result.session_id} {result.outcome:6s} "
                    f"approval_granted={result.approval_granted} "
                    f"events={result.event_count}",
                    file=sys.stderr,
                )
        exporter = tel.exporter

    manifest = RunManifest(
        schema_version=SPAN_FILE_SCHEMA_VERSION,
        contract=SPAN_CONTRACT_VERSION,
        run_id=run_id,
        scenario_name=catalogue.scenario_name,
        scenario_version=catalogue.scenario_version,
        seed=seed,
        session_count=count,
        catalogue_sha256=catalogue_sha,
        seed_data_sha256=seed_sha,
        model_id=model_spec.model_id,
        provider=model_spec.provider,
        genai_semconv=genai_semconv,
        files=tuple(exporter.written),
    )
    write_manifest(out_dir, manifest)
    return out_dir

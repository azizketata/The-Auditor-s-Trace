"""The only place that writes the ``at.*`` contract onto spans.

Every governance event in the fleet goes through :func:`event_span`; nothing
else touches layer-A span attributes. The function allocates the sequence
number and simulated timestamp, validates the event against the contract
*before* anything is written, and enforces the declaration invariant (each
object id declared exactly once per trace) by construction rather than by
test.

Layer-A spans deliberately set nothing from the standard vocabularies beyond
``openinference.span.kind`` and ``session.id`` — the layering rule the Phase 3
mapper keys off.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from opentelemetry.trace import Span, Tracer

from auditors_trace.model.ocel_schema import EventType, ObjectType
from auditors_trace.model.span_contract import (
    OPENINFERENCE_SESSION_ID,
    OPENINFERENCE_SPAN_KIND,
    ROLE_EVENT,
    ROLE_SESSION_ROOT,
    SPAN_ROLE,
    AttrValue,
    EventSpec,
    O2ORef,
    ObjectRef,
    SessionScope,
    SpanContractError,
    flatten_event,
    flatten_scope,
    validate_event,
)
from auditors_trace.scenario.clock import SimClock


@dataclass(slots=True)
class SessionContext:
    """Mutable per-session emission state."""

    scope: SessionScope
    tracer: Tracer
    clock: SimClock
    seq: int = 0
    declared: set[str] = field(default_factory=set)
    referenced: set[str] = field(default_factory=set)
    event_count: int = 0


def _scope_attributes(scope: SessionScope, role: str) -> dict[str, AttrValue]:
    attrs = flatten_scope(scope)
    attrs[SPAN_ROLE] = role
    attrs[OPENINFERENCE_SESSION_ID] = scope.session_id
    return attrs


@contextmanager
def session_span(tracer: Tracer, scope: SessionScope) -> Iterator[Span]:
    """The per-session root span. Every other span in the trace nests under it."""
    attrs = _scope_attributes(scope, ROLE_SESSION_ROOT)
    attrs[OPENINFERENCE_SPAN_KIND] = "CHAIN"
    with tracer.start_as_current_span(f"session {scope.session_id}", attributes=attrs) as span:
        yield span


def next_event_id(ctx: SessionContext, event_type: EventType, seq: int) -> str:
    """Globally unique, attribute-derived event id — never generation order."""
    return f"EVT-{ctx.scope.session_id}-{seq:03d}-{event_type.value}"


def declare_once(ctx: SessionContext, ref: ObjectRef) -> ObjectRef:
    """Enforce one declaration per object id per trace, at write time."""
    if ref.declares:
        if ref.object_id in ctx.declared:
            raise SpanContractError(
                f"object {ref.object_id!r} declared twice in session {ctx.scope.session_id}"
            )
        ctx.declared.add(ref.object_id)
    ctx.referenced.add(ref.object_id)
    return ref


@contextmanager
def event_span(
    ctx: SessionContext,
    *,
    event_type: EventType,
    name: str,
    actor: tuple[ObjectType, str] | None = None,
    attributes: Mapping[str, AttrValue] | None = None,
    objects: Sequence[ObjectRef] = (),
    o2o: Sequence[O2ORef] = (),
    span_kind: str = "CHAIN",
) -> Iterator[Span]:
    """Emit one OCEL event as a layer-A span.

    Validation happens before the span is started, so a contract violation
    never produces a half-written span.
    """
    ctx.seq += 1
    seq = ctx.seq
    time = ctx.clock.stamp(event_type)
    spec = EventSpec(
        event_type=event_type,
        event_id=next_event_id(ctx, event_type, seq),
        seq=seq,
        time=time,
        actor_id=actor[1] if actor else None,
        actor_type=actor[0] if actor else None,
        attributes=tuple((attributes or {}).items()),
        objects=tuple(declare_once(ctx, ref) for ref in objects),
        o2o=tuple(o2o),
    )
    validate_event(spec)
    for rel in spec.o2o:
        ctx.referenced.add(rel.source_id)
        ctx.referenced.add(rel.target_id)

    attrs = _scope_attributes(ctx.scope, ROLE_EVENT)
    attrs[OPENINFERENCE_SPAN_KIND] = span_kind
    attrs.update(flatten_event(spec))
    with ctx.tracer.start_as_current_span(name, attributes=attrs) as span:
        yield span
    ctx.event_count += 1


def check_declarations(ctx: SessionContext) -> None:
    """Verify every referenced object id was declared somewhere in the trace.

    Called at session end; because a reference may legitimately precede its
    declaration (request_approval cites the decision before make_decision
    declares it), this cannot be checked per-event.
    """
    undeclared = ctx.referenced - ctx.declared
    if undeclared:
        raise SpanContractError(
            f"session {ctx.scope.session_id}: referenced but never declared: {sorted(undeclared)}"
        )


def langchain_config(ctx: SessionContext, run_name: str, agent_role: str) -> RunnableConfig:
    """The config for every model/tool invocation inside an event span.

    ``metadata.session_id`` is what makes the OpenInference instrumentor emit
    ``session.id`` (and thence ``gen_ai.conversation.id``) on layer-B spans.

    ``callbacks`` is explicitly emptied: inside a LangGraph node the parent
    run's callbacks propagate via a contextvar, and a child run would be
    parented to the node's own chain span instead of the ambient layer-A event
    span. An empty list severs the run-tree linkage; the instrumentor's
    patched callback manager still injects its handler, so the invocation is
    traced — parented to the current layer-A span, which is the layering rule.
    """
    config: RunnableConfig = {
        "run_name": run_name,
        "callbacks": [],
        "metadata": {
            "session_id": ctx.scope.session_id,
            "at_agent_role": agent_role,
        },
    }
    return config


def set_attr(span: Span, key: str, value: Any) -> None:
    """Set one extra attribute on an already-open layer-A span."""
    span.set_attribute(key, value)

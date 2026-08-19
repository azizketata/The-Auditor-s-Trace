"""Span trees to one OCEL 2.0 log, coverage report, and span index.

Phase 3 deliverable. Satisfies claim C2 (the mapping is lossless for
governance attributes). A pure function of span-file content — Phase 4
re-maps injected span files through exactly this code (B1), so nothing here
may consult the environment, the clock, or unsorted iteration.

Mapping rules:

- A span is an OCEL event iff it carries ``at.event.type`` (the at-span/1
  layering rule; BUILD-PLAN's span-kind heuristic is superseded). Event
  order and identity come from ``at.event.seq`` / ``at.event.id``; the
  timestamp is the simulated clock's ``at.event.time``, never span nanos.
- Two passes per trace, because references may precede declarations
  (``request_approval`` cites the decision before ``make_decision``
  declares it). A pure declaration (qualifier ``None``, ``declares`` true)
  materialises as a synthesized ``Qualifier.DECLARES`` relation on its
  declaring event — spike constraint G1: pm4py exporters silently delete
  relation-less objects.
- Objects merge across traces: the same object id must be declared with the
  same type and attributes everywhere, or the run is rejected. O2O triples
  deduplicate across traces (an identical edge twice carries no
  information; the model forbids duplicates).
- Layer-B spans attribute to the nearest layer-A ancestor: an event span's
  subtree feeds that event's enrichment ids; the session-root subtree (the
  LangGraph CHAIN spans) feeds the session's. Their attributes flow through
  ``normalise_attributes`` into the coverage report.
- ``validate_event`` runs on every parsed event and ``OCELLog`` validation
  is the final gate. Raised, never repaired.

The span index exists because the frozen OCEL vocabulary carries no span
ids, while every evidence record (BUILD-PLAN section 8) must cite
``otel_trace_id`` and ``otel_span_ids`` — it is the deterministic sidecar
that links OCEL events back to the telemetry they came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auditors_trace.ingest.attribute_map import (
    CoverageReport,
    NormalisedSpan,
    build_coverage,
    normalise_attributes,
)
from auditors_trace.ingest.otel_reader import IngestError, Span, SpanTree
from auditors_trace.model.log import (
    OCELEvent,
    OCELLog,
    OCELObject,
    OCELRelation,
    canonical_json,
)
from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import (
    EVENT_TYPE,
    ROLE_SESSION_ROOT,
    SPAN_ROLE,
    AttrValue,
    EventSpec,
    O2ORef,
    parse_event,
    parse_scope,
    validate_event,
)

if TYPE_CHECKING:
    from auditors_trace.model.span_contract import SessionScope


@dataclass(frozen=True, slots=True)
class EventSpanRef:
    """Where one OCEL event lives in the telemetry."""

    event_id: str
    trace_id: str
    span_id: str
    enrichment_span_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionSpanRef:
    """The session root and its non-event enrichment subtree."""

    session_id: str
    trace_id: str
    root_span_id: str
    session_span_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpanIndex:
    """The OCEL-to-OTel linkage every evidence record cites."""

    schema_version: int
    sessions: tuple[SessionSpanRef, ...]
    events: tuple[EventSpanRef, ...]


def span_index_json(index: SpanIndex) -> str:
    """Render the span index as one canonical-JSON line (plus newline)."""
    payload = {
        "schema_version": index.schema_version,
        "sessions": [
            {
                "session_id": s.session_id,
                "trace_id": s.trace_id,
                "root_span_id": s.root_span_id,
                "session_span_ids": list(s.session_span_ids),
            }
            for s in index.sessions
        ],
        "events": [
            {
                "event_id": e.event_id,
                "trace_id": e.trace_id,
                "span_id": e.span_id,
                "enrichment_span_ids": list(e.enrichment_span_ids),
            }
            for e in index.events
        ],
    }
    return canonical_json(payload) + "\n"


@dataclass(frozen=True, slots=True)
class MappedRun:
    """Everything one mapping pass produces."""

    log: OCELLog
    coverage: CoverageReport
    span_index: SpanIndex


@dataclass(frozen=True, slots=True)
class _Declaration:
    object_type: ObjectType
    attributes: tuple[tuple[str, AttrValue], ...]
    declaring_event_id: str
    source: str


def _session_scope(tree: SpanTree) -> SessionScope:
    scope = parse_scope(tree.root.attributes)
    if scope is None:
        raise IngestError(
            f"{tree.root.source}: trace {tree.trace_id} root carries no at.* session "
            "scope; this is not an at-span/1 trace"
        )
    role = tree.root.attributes.get(SPAN_ROLE)
    if role != ROLE_SESSION_ROOT:
        raise IngestError(
            f"{tree.root.source}: trace root has at.span.role={role!r}, expected "
            f"{ROLE_SESSION_ROOT!r}"
        )
    if EVENT_TYPE in tree.root.attributes:
        raise IngestError(f"{tree.root.source}: the session root must not be an event")
    return scope


def _events_of(tree: SpanTree, scope: SessionScope) -> list[tuple[Span, EventSpec]]:
    events: list[tuple[Span, EventSpec]] = []
    for span in tree.spans:
        spec = parse_event(span.attributes)
        if spec is None:
            continue
        validate_event(spec)
        span_scope = parse_scope(span.attributes)
        if span_scope != scope:
            raise IngestError(f"{span.source}: event span scope disagrees with its session root")
        events.append((span, spec))

    seqs = sorted(spec.seq for _, spec in events)
    if seqs != list(range(1, len(seqs) + 1)):
        raise IngestError(
            f"trace {tree.trace_id}: at.event.seq values {seqs} are not dense 1..N; "
            "an incomplete session cannot be evidence"
        )
    events.sort(key=lambda pair: pair[1].seq)
    return events


def _nearest_event_ancestor(
    span: Span, parents: dict[str, Span], event_span_ids: set[str], root_id: str
) -> str:
    cursor = span.parent_span_id
    while cursor is not None:
        if cursor in event_span_ids or cursor == root_id:
            return cursor
        parent = parents.get(cursor)
        cursor = parent.parent_span_id if parent is not None else None
    raise IngestError(  # pragma: no cover - build_span_trees guarantees reachability
        f"{span.source}: no layer-A ancestor found"
    )


def map_span_trees(trees: tuple[SpanTree, ...]) -> MappedRun:
    """Convert span trees into one OCEL 2.0 log, coverage report, and index."""
    declarations: dict[str, _Declaration] = {}
    events: list[OCELEvent] = []
    o2o: set[O2ORef] = set()
    layer_a_coverage: list[tuple[EventType, frozenset[str]]] = []
    layer_b: list[NormalisedSpan] = []
    event_refs: list[EventSpanRef] = []
    session_refs: list[SessionSpanRef] = []
    seen_sessions: dict[str, str] = {}
    seen_event_ids: set[str] = set()

    for tree in trees:
        scope = _session_scope(tree)
        if scope.session_id in seen_sessions:
            raise IngestError(
                f"session id {scope.session_id!r} appears in traces "
                f"{seen_sessions[scope.session_id]} and {tree.trace_id}; session ids "
                "must be unique across a run"
            )
        seen_sessions[scope.session_id] = tree.trace_id

        tree_events = _events_of(tree, scope)

        # Pass 1: collect declarations (references may precede declarations).
        for span, spec in tree_events:
            for ref in spec.objects:
                if not ref.declares:
                    continue
                existing = declarations.get(ref.object_id)
                incoming = _Declaration(
                    object_type=ref.object_type,
                    attributes=ref.attributes,
                    declaring_event_id=spec.event_id,
                    source=span.source,
                )
                if existing is None:
                    declarations[ref.object_id] = incoming
                elif (
                    existing.object_type is not incoming.object_type
                    or existing.attributes != incoming.attributes
                ):
                    raise IngestError(
                        f"{span.source}: conflicting redeclaration of object "
                        f"{ref.object_id!r} (first declared at {existing.source}); "
                        "one object id must mean one object"
                    )

        # Pass 2: events, relations, o2o; synthesize DECLARES for pure declarations.
        for span, spec in tree_events:
            if spec.event_id in seen_event_ids:
                raise IngestError(
                    f"{span.source}: duplicate event id {spec.event_id!r} across the run"
                )
            seen_event_ids.add(spec.event_id)

            relations: list[OCELRelation] = []
            for ref in spec.objects:
                if ref.qualifier is not None:
                    relations.append(OCELRelation(object_id=ref.object_id, qualifier=ref.qualifier))
                elif ref.declares:
                    relations.append(
                        OCELRelation(object_id=ref.object_id, qualifier=Qualifier.DECLARES)
                    )
            events.append(
                OCELEvent(
                    event_id=spec.event_id,
                    event_type=spec.event_type,
                    timestamp=spec.time,
                    attributes=spec.attributes,
                    relations=tuple(relations),
                )
            )
            o2o.update(spec.o2o)
            present = frozenset(key for key in span.attributes if isinstance(key, str))
            layer_a_coverage.append((spec.event_type, present))

        # Referenced-but-never-declared ids (per trace, after both passes).
        referenced = {ref.object_id for _, spec in tree_events for ref in spec.objects}
        undeclared = sorted(referenced - set(declarations))
        if undeclared:
            raise IngestError(
                f"trace {tree.trace_id}: object id(s) {undeclared} are referenced "
                "but never declared in the run"
            )

        # Enrichment attribution + span index.
        parents = {s.span_id: s for s in tree.spans}
        event_span_ids = {span.span_id for span, _ in tree_events}
        enrichment: dict[str, list[str]] = {}
        session_bucket: list[str] = []
        for span in tree.spans:
            if span.span_id in event_span_ids or span.span_id == tree.root.span_id:
                continue
            anchor = _nearest_event_ancestor(span, parents, event_span_ids, tree.root.span_id)
            if anchor == tree.root.span_id:
                session_bucket.append(span.span_id)
            else:
                enrichment.setdefault(anchor, []).append(span.span_id)
            layer_b.append(normalise_attributes(span.attributes))

        session_refs.append(
            SessionSpanRef(
                session_id=scope.session_id,
                trace_id=tree.trace_id,
                root_span_id=tree.root.span_id,
                session_span_ids=tuple(sorted(session_bucket)),
            )
        )
        for span, spec in tree_events:
            event_refs.append(
                EventSpanRef(
                    event_id=spec.event_id,
                    trace_id=tree.trace_id,
                    span_id=span.span_id,
                    enrichment_span_ids=tuple(sorted(enrichment.get(span.span_id, ()))),
                )
            )

    objects = tuple(
        OCELObject(
            object_id=object_id,
            object_type=decl.object_type,
            attributes=decl.attributes,
        )
        for object_id, decl in sorted(declarations.items())
    )
    log = OCELLog(
        events=tuple(events),
        objects=objects,
        o2o=tuple(sorted(o2o, key=lambda r: (r.qualifier.value, r.source_id, r.target_id))),
    )
    coverage = build_coverage(layer_a_coverage, layer_b)
    span_index = SpanIndex(
        schema_version=1,
        sessions=tuple(sorted(session_refs, key=lambda s: s.session_id)),
        events=tuple(sorted(event_refs, key=lambda e: e.event_id)),
    )
    return MappedRun(log=log, coverage=coverage, span_index=span_index)

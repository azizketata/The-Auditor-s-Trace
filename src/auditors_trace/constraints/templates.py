"""The five agent-specific constraint templates.

Phase 5 deliverable, built in the order T1, T4, T5, T2, T3 (section 9). Every
template is a pure function ``(log, rule) -> list[Violation]``. All iteration is
sorted. No template may reference wall-clock time.

Specified in BUILD-PLAN.md section 6; conceptual origin is OC-DECLARE (Küsters
and van der Aalst, BPM 2025), instantiated here for agent fleets.

House rules the implementations share:

- Relation lookups always name the specific semantic qualifier (``reads``,
  ``approves``, ...), never "any relation to type X" — this is what keeps the
  pm4py-materialisation ``declares`` relations out of every predicate.
- Timestamps are canonical whole-second UTC strings, so lexicographic
  comparison IS chronological; "strictly before" means ``timestamp <``
  alone (BUILD-PLAN section 6: ``e'.timestamp < e.timestamp``), and an
  equal-second tie is not before — independent of event-id spelling.
  Event ids participate only in tie-breaking DISPLAY order (citations,
  canonical sort), never in the temporal predicate itself.
- Event ids are opaque: their numeric infixes are pre-injection sequence
  numbers and must never be parsed for ordering.
- Each violation cites exactly the evidence anchor the violation catalogue
  pre-registered for its fault class — the ground-truth join
  (``eval.metrics.exact_match``) is exact, so anchor drift is recall loss.
"""

from __future__ import annotations

from dataclasses import dataclass

from auditors_trace.constraints.ruleset import (
    Rule,
    Severity,
    T1Params,
    T2Params,
    T3Params,
    T4Params,
    T5Params,
)
from auditors_trace.model.log import OCELEvent, OCELLog, events_sorted
from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier


@dataclass(frozen=True, slots=True)
class Violation:
    """A constraint breach, carrying the exact event and object ids involved.

    The shared output format of every detection system (engine, flattened
    DECLARE, OC-DFG, LLM judge) and the renderer's per-violation input, so the
    field set is deliberately constructible from plain strings without an
    :class:`OCELLog` in hand. ``(constraint_id, set(ocel_event_ids))`` is THE
    ground-truth join key (``eval.metrics.exact_match``); everything else is
    context, never matched on. OTel span ids and policy/model context are
    renderer INPUTS (the span-index sidecar, keyed by event id) rather than
    fields here — a judge cannot know them, and the join key must be producible
    by every system alike.
    """

    #: Which constraint was breached, e.g. ``"T1.synchronised_approval"``.
    constraint_id: str
    #: The pre-registered evidence anchor(s), chronological. Never empty.
    ocel_event_ids: tuple[str, ...]
    #: Involved objects in the template's documented semantic order.
    ocel_object_ids: tuple[str, ...] = ()
    #: ``""`` when the producer cannot know it (flattened baselines, judge).
    session_id: str = ""
    #: From the rule that fired; producers without a rule keep the default.
    severity: Severity = Severity.MEDIUM
    #: Deterministic diagnostic for humans. Never hashed, never matched.
    detail: str = ""


def violation_sort_key(violation: Violation) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """The canonical output order: (constraint id, first event id) per the
    spec, extended by the full id tuples so the order is total and stable."""
    return (violation.constraint_id, violation.ocel_event_ids, violation.ocel_object_ids)


@dataclass(frozen=True)
class LogIndex:
    """One deterministic pass over the log, shared by every template.

    Internal to the constraints package (``standard.py`` reuses it). Built
    per template call — the frozen ``(log, rule)`` signature keeps templates
    pure, and six O(n) passes over a few thousand events cost nothing.
    """

    #: ``events_sorted(log)``: (timestamp, event_id) order.
    events: tuple[OCELEvent, ...]
    #: Object id -> object, for type filtering.
    objects_by_id: dict[str, object]
    #: Object id -> its attributes as a plain dict.
    object_attrs: dict[str, dict[str, object]]
    #: Event type -> events of that type, in global sorted order.
    events_by_type: dict[EventType, tuple[OCELEvent, ...]]
    #: Event id -> the Session object it is ``within``. Events without a
    #: ``within`` relation (``session_start`` relates via ``declares``) are
    #: absent — no current template anchors on them.
    session_of_event: dict[str, str]
    #: Object id -> its declared object type.
    type_of_object: dict[str, ObjectType]


def build_index(log: OCELLog) -> LogIndex:
    """Index the log once. Pure; every collection is deterministically ordered."""
    events = events_sorted(log)
    objects_by_id: dict[str, object] = {}
    object_attrs: dict[str, dict[str, object]] = {}
    type_of_object: dict[str, ObjectType] = {}
    for obj in log.objects:
        objects_by_id[obj.object_id] = obj
        object_attrs[obj.object_id] = dict(obj.attributes)
        type_of_object[obj.object_id] = obj.object_type

    grouped: dict[EventType, list[OCELEvent]] = {}
    session_of_event: dict[str, str] = {}
    for event in events:
        grouped.setdefault(event.event_type, []).append(event)
        for relation in event.relations:
            if (
                relation.qualifier is Qualifier.WITHIN
                and type_of_object.get(relation.object_id) is ObjectType.SESSION
            ):
                session_of_event[event.event_id] = relation.object_id
                break  # relations are sorted by object id; one session per event
    events_by_type = {event_type: tuple(items) for event_type, items in grouped.items()}
    return LogIndex(
        events=events,
        objects_by_id=objects_by_id,
        object_attrs=object_attrs,
        events_by_type=events_by_type,
        session_of_event=session_of_event,
        type_of_object=type_of_object,
    )


def related_ids(
    event: OCELEvent, qualifier: Qualifier, index: LogIndex, object_type: ObjectType
) -> tuple[str, ...]:
    """Object ids the event relates to via exactly this qualifier and type.

    Naming the qualifier is the ``declares``-exclusion rule in action: a
    materialisation relation can never satisfy a semantic lookup. Order is
    the event's relation order (sorted by object id).
    """
    return tuple(
        relation.object_id
        for relation in event.relations
        if relation.qualifier is qualifier
        and index.type_of_object.get(relation.object_id) is object_type
    )


def strictly_before(first: OCELEvent, second: OCELEvent) -> bool:
    """Chronological order with the equal-second tie counting as NOT before.

    Timestamps only — never the event ids. A (timestamp, event_id) tuple
    comparison here would let id spelling decide equal-second ties, and
    corpus-format ids always sort the earlier-sequenced event first, which
    would silently count a same-second approval as prior (adversarial
    review, 19 Aug 2026).
    """
    return first.timestamp < second.timestamp


def event_attrs(event: OCELEvent) -> dict[str, object]:
    """The event's attributes as a plain dict."""
    return dict(event.attributes)


def t1_synchronised_approval(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every decision requiring approval has a prior grant by an authorised role.

    Three pre-registered citation cases mirror the fault classes:

    - V1 (no approval anywhere): cite the decision event, object the decision.
    - V2 (an in-session approval exists but does not satisfy — wrong decision,
      or not strictly prior): cite approval + decision chronologically,
      objects (own decision, approves-target) deduplicated.
    - V3 (a prior approval of this decision exists, every approver role
      unauthorised): cite the approval alone, object its approver.
    """
    params = T1Params.model_validate(rule.params)
    index = build_index(log)
    allowed = frozenset(params.allowed_roles)
    outcomes = frozenset(params.decision_outcomes)
    approvals = index.events_by_type.get(EventType.GRANT_APPROVAL, ())

    violations: list[Violation] = []
    for event in index.events_by_type.get(EventType.MAKE_DECISION, ()):
        outcome = event_attrs(event).get("outcome")
        if not isinstance(outcome, str) or outcome not in outcomes:
            continue
        session_id = index.session_of_event.get(event.event_id, "")
        for decision_id in related_ids(
            event, Qualifier.PRODUCES, index, ObjectType.CREDIT_DECISION
        ):
            prior = tuple(
                approval
                for approval in approvals
                if decision_id
                in related_ids(approval, Qualifier.APPROVES, index, ObjectType.CREDIT_DECISION)
                and strictly_before(approval, event)
            )
            if any(
                index.object_attrs.get(approver_id, {}).get("role") in allowed
                for approval in prior
                for approver_id in related_ids(
                    approval, Qualifier.PERFORMED_BY, index, ObjectType.HUMAN_APPROVER
                )
            ):
                continue
            if prior:
                first = prior[0]
                approvers = related_ids(
                    first, Qualifier.PERFORMED_BY, index, ObjectType.HUMAN_APPROVER
                )
                roles = sorted(
                    str(index.object_attrs.get(approver_id, {}).get("role", ""))
                    for approver_id in approvers
                )
                violations.append(
                    Violation(
                        constraint_id=rule.constraint_id,
                        ocel_event_ids=(first.event_id,),
                        ocel_object_ids=approvers,
                        session_id=index.session_of_event.get(first.event_id, session_id),
                        severity=rule.severity,
                        detail=(
                            f"decision {decision_id} approved only by unauthorised "
                            f"role(s) {', '.join(roles) or '(none)'}"
                        ),
                    )
                )
                continue
            in_session = tuple(
                approval
                for approval in approvals
                if session_id and index.session_of_event.get(approval.event_id) == session_id
            )
            if in_session:
                first = in_session[0]
                cited = sorted((first, event), key=lambda e: (e.timestamp, e.event_id))
                targets = related_ids(first, Qualifier.APPROVES, index, ObjectType.CREDIT_DECISION)
                objects = (decision_id, *(t for t in targets if t != decision_id))
                if targets and all(t == decision_id for t in targets):
                    detail = f"approval for decision {decision_id} is not strictly prior"
                else:
                    detail = (
                        f"the session's approval endorses {', '.join(targets) or 'nothing'}, "
                        f"not decision {decision_id}"
                    )
                violations.append(
                    Violation(
                        constraint_id=rule.constraint_id,
                        ocel_event_ids=tuple(e.event_id for e in cited),
                        ocel_object_ids=objects,
                        session_id=session_id,
                        severity=rule.severity,
                        detail=detail,
                    )
                )
                continue
            violations.append(
                Violation(
                    constraint_id=rule.constraint_id,
                    ocel_event_ids=(event.event_id,),
                    ocel_object_ids=(decision_id,),
                    session_id=session_id,
                    severity=rule.severity,
                    detail=f"decision {decision_id} ({outcome}) has no approval at all",
                )
            )
    return sorted(violations, key=violation_sort_key)


def t2_mandatory_data_coverage(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every decision's application has retrievals covering all required sources.

    Coverage counts a source type only when a ``retrieve_data`` event
    ``reads`` a resource of that type AND ``concerns`` the application; the
    resource OBJECT's ``resource_type`` is authoritative. Cites the decision
    event with an empty object tuple — the V4 anchor as pre-registered.
    """
    params = T2Params.model_validate(rule.params)
    index = build_index(log)

    coverage: dict[str, set[str]] = {}
    for retrieval in index.events_by_type.get(EventType.RETRIEVE_DATA, ()):
        read_types = {
            str(index.object_attrs.get(resource_id, {}).get("resource_type", ""))
            for resource_id in related_ids(
                retrieval, Qualifier.READS, index, ObjectType.DATA_RESOURCE
            )
        }
        for application_id in related_ids(
            retrieval, Qualifier.CONCERNS, index, ObjectType.APPLICATION
        ):
            coverage.setdefault(application_id, set()).update(read_types)

    violations: list[Violation] = []
    for event in index.events_by_type.get(EventType.MAKE_DECISION, ()):
        covered: set[str] = set()
        for application_id in related_ids(event, Qualifier.CONCERNS, index, ObjectType.APPLICATION):
            covered |= coverage.get(application_id, set())
        missing = [source for source in params.required_sources if source not in covered]
        if missing:
            violations.append(
                Violation(
                    constraint_id=rule.constraint_id,
                    ocel_event_ids=(event.event_id,),
                    ocel_object_ids=(),
                    session_id=index.session_of_event.get(event.event_id, ""),
                    severity=rule.severity,
                    detail=f"required sources never retrieved: {', '.join(sorted(missing))}",
                )
            )
    return sorted(violations, key=violation_sort_key)


def _cycle_edge_events(
    handoffs: tuple[OCELEvent, ...], index: LogIndex, excluded: set[str]
) -> tuple[tuple[OCELEvent, ...], tuple[str, ...]]:
    """Handoffs lying on a directed from->to cycle, and the agents involved.

    An edge is on a cycle iff its source is reachable from its target. The
    ``excluded`` handoffs (already cited as misdirected by the orphan arm)
    do not contribute edges — this is the single-violation guarantee: a
    repointed handoff explains the orphan it caused; it must not be charged
    a second time as a cycle.
    """
    edges: list[tuple[str, str, OCELEvent]] = []
    adjacency: dict[str, list[str]] = {}
    for handoff in handoffs:
        if handoff.event_id in excluded:
            continue
        for source in related_ids(handoff, Qualifier.FROM, index, ObjectType.AGENT):
            for target in related_ids(handoff, Qualifier.TO, index, ObjectType.AGENT):
                edges.append((source, target, handoff))
                adjacency.setdefault(source, []).append(target)

    def reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            for neighbour in adjacency.get(node, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        return seen

    on_cycle = [(source, target, h) for source, target, h in edges if source in reachable(target)]
    events = tuple(h for h in handoffs if any(h.event_id == edge[2].event_id for edge in on_cycle))
    agents = tuple(sorted({node for source, target, _ in on_cycle for node in (source, target)}))
    return events, agents


def t3_delegation_integrity(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every agent action is preceded by a handoff, and no handoff chain cycles.

    Per session, per acting agent (events ``performed_by`` an Agent object —
    approval verdicts are performed by HumanApprovers and are out of scope):
    the agent is compliant if its role is an entrypoint role or some handoff
    with ``to`` = the agent strictly precedes its first performed event.
    Otherwise ONE violation per (session, agent), with the citation split by
    what the evidence shows:

    - the orphan's immediately preceding session event is a handoff
      (necessarily to someone else) -> cite (that handoff, first event) —
      the V7 repoint anchor;
    - anything else -> cite (first event,) alone — the V7 delete anchor.

    The cycle arm then checks the per-session from->to handoff graph with
    every orphan-cited handoff EXCLUDED (10 of 15 live repoint sessions would
    otherwise double-fire) and emits one violation per session citing the
    cycle's handoffs chronologically.
    """
    params = T3Params.model_validate(rule.params)
    index = build_index(log)
    entrypoints = frozenset(params.entrypoint_roles)

    sessions: dict[str, list[OCELEvent]] = {}
    for event in index.events:
        session_id = index.session_of_event.get(event.event_id)
        if session_id is not None:
            sessions.setdefault(session_id, []).append(event)

    violations: list[Violation] = []
    for session_id in sorted(sessions):
        events = sessions[session_id]
        positions = {event.event_id: position for position, event in enumerate(events)}
        handoffs = tuple(e for e in events if e.event_type is EventType.HANDOFF)

        first_event_of_agent: dict[str, OCELEvent] = {}
        for event in events:
            for agent_id in related_ids(event, Qualifier.PERFORMED_BY, index, ObjectType.AGENT):
                first_event_of_agent.setdefault(agent_id, event)

        cited_handoffs: set[str] = set()
        for agent_id, first_event in first_event_of_agent.items():
            role = str(index.object_attrs.get(agent_id, {}).get("role", ""))
            if role in entrypoints:
                continue
            if any(
                agent_id in related_ids(handoff, Qualifier.TO, index, ObjectType.AGENT)
                and strictly_before(handoff, first_event)
                for handoff in handoffs
            ):
                continue
            position = positions[first_event.event_id]
            predecessor = events[position - 1] if position > 0 else None
            if predecessor is not None and predecessor.event_type is EventType.HANDOFF:
                cited_handoffs.add(predecessor.event_id)
                targets = related_ids(predecessor, Qualifier.TO, index, ObjectType.AGENT)
                violations.append(
                    Violation(
                        constraint_id=rule.constraint_id,
                        ocel_event_ids=(predecessor.event_id, first_event.event_id),
                        ocel_object_ids=(agent_id,),
                        session_id=session_id,
                        severity=rule.severity,
                        detail=(
                            f"the preceding handoff delegates to "
                            f"{', '.join(targets) or 'nobody'}, yet {agent_id} "
                            f"({role or 'no role'}) acted next"
                        ),
                    )
                )
            else:
                violations.append(
                    Violation(
                        constraint_id=rule.constraint_id,
                        ocel_event_ids=(first_event.event_id,),
                        ocel_object_ids=(agent_id,),
                        session_id=session_id,
                        severity=rule.severity,
                        detail=(
                            f"{agent_id} ({role or 'no role'}) acted without any "
                            "preceding handoff and is not an entrypoint"
                        ),
                    )
                )

        cycle_handoffs, cycle_agents = _cycle_edge_events(handoffs, index, cited_handoffs)
        if cycle_handoffs:
            violations.append(
                Violation(
                    constraint_id=rule.constraint_id,
                    ocel_event_ids=tuple(h.event_id for h in cycle_handoffs),
                    ocel_object_ids=cycle_agents,
                    session_id=session_id,
                    severity=rule.severity,
                    detail=(f"delegation handoffs form a cycle over {', '.join(cycle_agents)}"),
                )
            )
    return sorted(violations, key=violation_sort_key)


def t4_reason_code_presence(log: OCELLog, rule: Rule) -> list[Violation]:
    """Every adverse decision carries reason codes and a referencing reasoning event.

    Both arms read authoritative sources: the ``reason_codes`` on the
    CreditDecision OBJECT (the event attribute stays populated under V6's
    variant A, by disclosed design — never read it) and the presence of an
    ``emit_reasoning`` event whose ``explains`` relation targets the same
    decision. One violation per decision regardless of how many arms fail,
    citing the decision event — the V6 anchor for both variants.
    """
    params = T4Params.model_validate(rule.params)
    index = build_index(log)
    adverse = frozenset(params.adverse_outcomes)

    explained: set[str] = set()
    for reasoning in index.events_by_type.get(EventType.EMIT_REASONING, ()):
        explained.update(
            related_ids(reasoning, Qualifier.EXPLAINS, index, ObjectType.CREDIT_DECISION)
        )

    violations: list[Violation] = []
    for event in index.events_by_type.get(EventType.MAKE_DECISION, ()):
        outcome = event_attrs(event).get("outcome")
        if not isinstance(outcome, str) or outcome not in adverse:
            continue
        for decision_id in related_ids(
            event, Qualifier.PRODUCES, index, ObjectType.CREDIT_DECISION
        ):
            codes = index.object_attrs.get(decision_id, {}).get("reason_codes", ())
            failures: list[str] = []
            if not codes:
                failures.append("empty reason_codes on the decision object")
            if decision_id not in explained:
                failures.append("no emit_reasoning event explains the decision")
            if failures:
                violations.append(
                    Violation(
                        constraint_id=rule.constraint_id,
                        ocel_event_ids=(event.event_id,),
                        ocel_object_ids=(decision_id,),
                        session_id=index.session_of_event.get(event.event_id, ""),
                        severity=rule.severity,
                        detail="; ".join(failures),
                    )
                )
    return sorted(violations, key=violation_sort_key)


def t5_prohibited_attribute_access(log: OCELLog, rule: Rule) -> list[Violation]:
    """No special-category resource is read without a recorded lawful basis.

    Scoped, per the spec, to ``retrieve_data`` and ``call_llm`` events that
    ``concern`` an Application. The ``call_llm`` arm is provably vacuous under
    the frozen qualifier matrix — no ``(call_llm, DataResource)`` pair exists,
    and the model rejects such a relation (pinned by test) — but the spec
    names it, so the loop is written defensively rather than narrowed. An
    absent or whitespace-only ``lawful_basis`` counts as missing.
    """
    params = T5Params.model_validate(rule.params)
    index = build_index(log)
    prohibited = frozenset(params.prohibited_classifications)

    violations: list[Violation] = []
    for event_type in (EventType.RETRIEVE_DATA, EventType.CALL_LLM):
        for event in index.events_by_type.get(event_type, ()):
            if not related_ids(event, Qualifier.CONCERNS, index, ObjectType.APPLICATION):
                continue
            for resource_id in related_ids(event, Qualifier.READS, index, ObjectType.DATA_RESOURCE):
                attrs = index.object_attrs.get(resource_id, {})
                if str(attrs.get("classification", "")) not in prohibited:
                    continue
                if str(attrs.get("lawful_basis", "")).strip():
                    continue
                violations.append(
                    Violation(
                        constraint_id=rule.constraint_id,
                        ocel_event_ids=(event.event_id,),
                        ocel_object_ids=(resource_id,),
                        session_id=index.session_of_event.get(event.event_id, ""),
                        severity=rule.severity,
                        detail=(
                            f"{event.event_type.value} read {resource_id} "
                            f"({attrs.get('classification')}) without a lawful basis"
                        ),
                    )
                )
    return sorted(violations, key=violation_sort_key)

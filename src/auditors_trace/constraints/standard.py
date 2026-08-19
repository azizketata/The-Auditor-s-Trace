"""Object-scoped analogues of classic DECLARE.

Phase 5 deliverable. Needed for the E1 expressiveness comparison against
case-centric DECLARE: these are the constraints that survive object-centricity
and whose flattened counterparts do not. ``object_absence`` additionally
carries ``STD.policy_version_current`` (V8) via its attribute-predicated form:
``violating_when: non_empty`` on ``PolicyVersion.effective_to`` flags any
``make_decision`` governed by a superseded policy, anchored on the decision
event only — exactly the pre-registered V8 evidence anchor.

"Relating to" an object means any relation EXCEPT ``declares``: the
materialisation relations pm4py requires are never semantic evidence. The one
place ``declares`` may appear is as a citation anchor in ``object_existence``
(the first event citing the anchor object locates it in time; it proves
nothing about the missing relation).
"""

from __future__ import annotations

from auditors_trace.constraints.ruleset import (
    ObjectAbsenceParams,
    ObjectExistenceParams,
    Rule,
    SynchronisedActivitiesParams,
)
from auditors_trace.constraints.templates import (
    LogIndex,
    Violation,
    build_index,
    strictly_before,
    violation_sort_key,
)
from auditors_trace.model.log import OCELEvent, OCELLog
from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier


def _semantic_events_relating(
    index: LogIndex, event_type: EventType, object_id: str
) -> tuple[OCELEvent, ...]:
    """Events of the given type relating to the object via any non-declares
    qualifier, in (timestamp, event_id) order."""
    return tuple(
        event
        for event in index.events_by_type.get(event_type, ())
        if any(
            relation.object_id == object_id and relation.qualifier is not Qualifier.DECLARES
            for relation in event.relations
        )
    )


def _attribute_violates(params: ObjectAbsenceParams, value: object) -> bool:
    if params.violating_when == "always":
        return True
    if params.violating_when == "non_empty":
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, tuple):
            return bool(value)
        return value is not None
    # "equals": the params model guarantees value is set; the guard keeps a
    # bypassed model (model_construct) from comparing against None.
    return params.value is not None and str(value) == params.value


def object_existence(log: OCELLog, rule: Rule) -> list[Violation]:
    """An object of the given type must exist for each anchor object.

    Anchor objects are checked for at least one o2o edge with the configured
    qualifier and direction whose far end has the related type. A violation
    cites the chronologically first event citing the anchor object (any
    qualifier — a pure declaration still locates the object in time), with
    the anchor object as the involved object.
    """
    params = ObjectExistenceParams.model_validate(rule.params)
    index = build_index(log)
    anchor_type = ObjectType(params.anchor_object_type)
    related_type = ObjectType(params.related_object_type)
    qualifier = Qualifier(params.o2o_qualifier)

    satisfied: set[str] = set()
    for relation in log.o2o:
        if relation.qualifier is not qualifier:
            continue
        if params.direction == "outgoing":
            anchor_id, far_id = relation.source_id, relation.target_id
        else:
            anchor_id, far_id = relation.target_id, relation.source_id
        if (
            index.type_of_object.get(anchor_id) is anchor_type
            and index.type_of_object.get(far_id) is related_type
        ):
            satisfied.add(anchor_id)

    violations: list[Violation] = []
    for object_id, object_type in index.type_of_object.items():
        if object_type is not anchor_type or object_id in satisfied:
            continue
        citing = next(
            (
                event
                for event in index.events
                if any(relation.object_id == object_id for relation in event.relations)
            ),
            None,
        )
        if citing is None:  # unreachable on a valid log (every object has an E2O)
            continue
        violations.append(
            Violation(
                constraint_id=rule.constraint_id,
                ocel_event_ids=(citing.event_id,),
                ocel_object_ids=(object_id,),
                session_id=index.session_of_event.get(citing.event_id, ""),
                severity=rule.severity,
                detail=(
                    f"{object_id} has no {params.direction} {qualifier.value} "
                    f"edge to a {related_type.value}"
                ),
            )
        )
    return sorted(violations, key=violation_sort_key)


def object_absence(log: OCELLog, rule: Rule) -> list[Violation]:
    """No object of the given type may relate to the anchor event.

    ``violating_when: always`` is classic absence; ``non_empty``/``equals``
    restrict the breach to targets whose attribute matches the predicate —
    the form ``STD.policy_version_current`` ships with.
    """
    params = ObjectAbsenceParams.model_validate(rule.params)
    index = build_index(log)
    anchor_type = EventType(params.anchor_event_type)
    object_type = ObjectType(params.object_type)
    qualifier = Qualifier(params.qualifier)

    violations: list[Violation] = []
    for event in index.events_by_type.get(anchor_type, ()):
        for relation in event.relations:
            if relation.qualifier is not qualifier:
                continue
            if index.type_of_object.get(relation.object_id) is not object_type:
                continue
            attrs = index.object_attrs.get(relation.object_id, {})
            value = attrs.get(params.attribute) if params.attribute else None
            if not _attribute_violates(params, value):
                continue
            if params.violating_when == "always":
                detail = (
                    f"{event.event_type.value} must not relate to a "
                    f"{object_type.value} via {qualifier.value}"
                )
            else:
                detail = (
                    f"{relation.object_id}.{params.attribute} = {value!r} breaches "
                    f"the {params.violating_when} predicate"
                )
            violations.append(
                Violation(
                    constraint_id=rule.constraint_id,
                    ocel_event_ids=(event.event_id,),
                    ocel_object_ids=(relation.object_id,),
                    session_id=index.session_of_event.get(event.event_id, ""),
                    severity=rule.severity,
                    detail=detail,
                )
            )
    return sorted(violations, key=violation_sort_key)


def synchronised_response(log: OCELLog, rule: Rule) -> list[Violation]:
    """Activity A on a shared object must be followed by activity B on that object."""
    params = SynchronisedActivitiesParams.model_validate(rule.params)
    index = build_index(log)
    activity_a = EventType(params.activity_a)
    activity_b = EventType(params.activity_b)
    object_type = ObjectType(params.object_type)

    violations: list[Violation] = []
    for object_id, otype in index.type_of_object.items():
        if otype is not object_type:
            continue
        followers = _semantic_events_relating(index, activity_b, object_id)
        for event in _semantic_events_relating(index, activity_a, object_id):
            if any(strictly_before(event, follower) for follower in followers):
                continue
            violations.append(
                Violation(
                    constraint_id=rule.constraint_id,
                    ocel_event_ids=(event.event_id,),
                    ocel_object_ids=(object_id,),
                    session_id=index.session_of_event.get(event.event_id, ""),
                    severity=rule.severity,
                    detail=(f"no {activity_b.value} follows {activity_a.value} on {object_id}"),
                )
            )
    return sorted(violations, key=violation_sort_key)


def synchronised_precedence(log: OCELLog, rule: Rule) -> list[Violation]:
    """Activity B on a shared object must be preceded by activity A on that object."""
    params = SynchronisedActivitiesParams.model_validate(rule.params)
    index = build_index(log)
    activity_a = EventType(params.activity_a)
    activity_b = EventType(params.activity_b)
    object_type = ObjectType(params.object_type)

    violations: list[Violation] = []
    for object_id, otype in index.type_of_object.items():
        if otype is not object_type:
            continue
        predecessors = _semantic_events_relating(index, activity_a, object_id)
        for event in _semantic_events_relating(index, activity_b, object_id):
            if any(strictly_before(predecessor, event) for predecessor in predecessors):
                continue
            violations.append(
                Violation(
                    constraint_id=rule.constraint_id,
                    ocel_event_ids=(event.event_id,),
                    ocel_object_ids=(object_id,),
                    session_id=index.session_of_event.get(event.event_id, ""),
                    severity=rule.severity,
                    detail=(f"no {activity_a.value} precedes {activity_b.value} on {object_id}"),
                )
            )
    return sorted(violations, key=violation_sort_key)

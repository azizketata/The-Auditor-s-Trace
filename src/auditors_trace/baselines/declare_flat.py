"""Case-centric DECLARE baseline.

Phase 7 deliverable. Flatten the OCEL log onto a case notion, then run
pm4py's declarative conformance against a DECLARE model discovered on the
clean split.

The point is not to win: it is to show which violations become invisible
after flattening. The measured mechanism (Phase 7 amendment, correcting the
pre-registered B7 premise) is **qualifier and object-identity loss, not
event loss**: on this corpus, Application flattening keeps every event and
every handoff (each handoff `concerns` an Application), but drops the
`ocel:qualifier` column and object identity entirely — so V2/V3/V5/V6/V8
(30/30 each) and most of V7 (16/30) leave a flattened activity sequence
byte-identical to a clean-split variant: 166/240 = 69.2% of injected
violations are control-flow-invisible.

Scoring is CASE-LEVEL (`eval.metrics.session_match`): pm4py's DECLARE
deviations carry template names and activity pairs but NO event ids and no
constraint-class semantics, so the exact ground-truth join is structurally
unwinnable for this baseline. Violations cite synthesised anchors under the
pre-registered rule below — deterministic and honest, never credited as
exact matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from auditors_trace.constraints.templates import Violation, build_index, violation_sort_key
from auditors_trace.model.io import to_pm4py
from auditors_trace.model.log import OCELLog
from auditors_trace.model.ocel_schema import ObjectType

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from auditors_trace.scenario.injector import GroundTruthViolation

CaseNotion = Literal["Application", "Session", "CreditDecision", "Agent"]
CASE_NOTIONS: Final[tuple[CaseNotion, ...]] = (
    "Application",
    "Session",
    "CreditDecision",
    "Agent",
)

_CASE: Final = "case:concept:name"
_ACTIVITY: Final = "concept:name"
_TIME: Final = "time:timestamp"
_EID: Final = "ocel:eid"


def flatten_to_xes(log: OCELLog, case_notion: CaseNotion) -> Any:
    """Flatten the log onto one object type, producing a pm4py event frame.

    Uses the in-memory bridge (`model.io.to_pm4py`) — no disk round trip,
    never `pm4py.read_ocel` (which mis-dispatches on OCEL 2.0 JSON), never
    `ocel_merge_duplicates` (which rewrites every event id). `ocel:eid`
    survives flattening; `ocel:qualifier` does not — that loss IS the E1
    result.
    """
    import pm4py

    return pm4py.ocel_flattening(to_pm4py(log), case_notion)


def discover_reference(clean_log: OCELLog, case_notion: CaseNotion) -> Mapping[str, Any]:
    """Discover the DECLARE reference model on the clean split's flattening."""
    import pm4py

    result: Mapping[str, Any] = pm4py.discover_declare(flatten_to_xes(clean_log, case_notion))
    return result


def _case_to_session(log: OCELLog, case_notion: CaseNotion) -> dict[str, str]:
    """Deterministic case-id -> session-id mapping per notion.

    Session cases ARE sessions; Application/CreditDecision objects map to the
    (sorted-first) session whose events reference them; the four Agent cases
    span every session and map to "" (no honest single session exists).
    """
    if case_notion == "Agent":
        return {}
    index = build_index(log)
    notion_type = ObjectType(case_notion)
    if case_notion == "Session":
        return {
            obj.object_id: obj.object_id
            for obj in log.objects
            if obj.object_type is ObjectType.SESSION
        }
    sessions_of_object: dict[str, set[str]] = {}
    for event in index.events:
        session_id = index.session_of_event.get(event.event_id)
        if session_id is None:
            continue
        for relation in event.relations:
            if index.type_of_object.get(relation.object_id) is notion_type:
                sessions_of_object.setdefault(relation.object_id, set()).add(session_id)
    return {object_id: sorted(sessions)[0] for object_id, sessions in sessions_of_object.items()}


def detect(
    log: OCELLog,
    *,
    case_notion: CaseNotion = "Application",
    reference: Mapping[str, Any],
) -> list[Violation]:
    """Run case-centric declarative conformance against the clean reference.

    One conformance pass per case (deterministic case order — pm4py's batch
    result list carries no case ids). Violations cite anchors under the
    PRE-REGISTERED synthesis rule: every `ocel:eid` in the deviating case
    whose activity is in the deviation's activity pair, chronological;
    fallback = the case's first event id (the `Violation` contract forbids
    empty citations). Scored via `session_match` only.
    """
    import pm4py

    flat = flatten_to_xes(log, case_notion)
    case_sessions = _case_to_session(log, case_notion)

    violations: list[Violation] = []
    for case_id in sorted(set(flat[_CASE])):
        case_frame = flat[flat[_CASE] == case_id].sort_values([_TIME, _EID])
        result = pm4py.conformance_declare(case_frame, reference)[0]
        for template, activities in result.get("deviations", ()):
            pair = {activities} if isinstance(activities, str) else set(activities)
            anchor_rows = case_frame[case_frame[_ACTIVITY].isin(pair)]
            anchors = tuple(dict.fromkeys(anchor_rows[_EID]))
            if not anchors:
                anchors = (str(case_frame.iloc[0][_EID]),)
            violations.append(
                Violation(
                    constraint_id=f"DECLARE.{str(template).lower()}",
                    ocel_event_ids=anchors,
                    session_id=case_sessions.get(str(case_id), ""),
                    detail=(f"{case_notion} case {case_id}: {template}({', '.join(sorted(pair))})"),
                )
            )
    return sorted(violations, key=violation_sort_key)


@dataclass(frozen=True, slots=True)
class MatrixCell:
    """One (case notion, fault class) cell of the E1 detectability matrix."""

    total: int
    control_flow_visible: int
    declare_flagged: int


@dataclass(frozen=True, slots=True)
class NotionStats:
    """Per-notion event duplication/loss counts (convergence/divergence)."""

    events_total: int
    events_kept: int
    events_dropped: int
    events_duplicated: int


@dataclass(frozen=True, slots=True)
class FlatteningMatrix:
    """The per-flattening x per-violation-class detectability matrix (E1)."""

    cells: Mapping[tuple[str, str], MatrixCell]
    notion_stats: Mapping[str, NotionStats]


def _case_sequences(flat: Any) -> dict[str, tuple[str, ...]]:
    sequences: dict[str, tuple[str, ...]] = {}
    for case_id in sorted(set(flat[_CASE])):
        case_frame = flat[flat[_CASE] == case_id].sort_values([_TIME, _EID])
        sequences[str(case_id)] = tuple(case_frame[_ACTIVITY])
    return sequences


def flattening_matrix(
    clean_log: OCELLog,
    eval_log: OCELLog,
    labels: Sequence[GroundTruthViolation],
    case_notions: Sequence[CaseNotion] = CASE_NOTIONS,
) -> FlatteningMatrix:
    """Build the E1 matrix: visibility and DECLARE detection per fault class.

    `control_flow_visible` uses the pre-registered variant-novelty
    definition: a fault is visible under a notion iff at least one flattened
    case containing any of its anchor event ids has an activity sequence
    that occurs in NO clean-split case. `declare_flagged` is session-credit:
    the conformance run implicated the fault's session.
    """
    cells: dict[tuple[str, str], MatrixCell] = {}
    notion_stats: dict[str, NotionStats] = {}

    for case_notion in case_notions:
        flat_clean = flatten_to_xes(clean_log, case_notion)
        flat_eval = flatten_to_xes(eval_log, case_notion)
        clean_variants = set(_case_sequences(flat_clean).values())
        eval_sequences = _case_sequences(flat_eval)

        total_rows = len(flat_eval)
        kept_ids = set(flat_eval[_EID])
        notion_stats[case_notion] = NotionStats(
            events_total=len(eval_log.events),
            events_kept=len(kept_ids),
            events_dropped=len(eval_log.events) - len(kept_ids),
            events_duplicated=total_rows - len(kept_ids),
        )

        cases_of_event: dict[str, set[str]] = {}
        for row in flat_eval[[_CASE, _EID]].itertuples(index=False):
            cases_of_event.setdefault(str(row[1]), set()).add(str(row[0]))

        reference = discover_reference(clean_log, case_notion)
        detected = detect(eval_log, case_notion=case_notion, reference=reference)
        flagged_sessions = {violation.session_id for violation in detected if violation.session_id}

        by_class: dict[str, list[GroundTruthViolation]] = {}
        for truth in labels:
            by_class.setdefault(truth.fault_class, []).append(truth)
        for fault_class, truths in sorted(by_class.items()):
            visible = 0
            flagged = 0
            for truth in truths:
                containing_cases = {
                    case_id
                    for event_id in truth.ocel_event_ids
                    for case_id in cases_of_event.get(event_id, ())
                }
                if any(
                    eval_sequences.get(case_id) not in clean_variants
                    for case_id in containing_cases
                ):
                    visible += 1
                if truth.session_id in flagged_sessions:
                    flagged += 1
            cells[(case_notion, fault_class)] = MatrixCell(
                total=len(truths), control_flow_visible=visible, declare_flagged=flagged
            )
    return FlatteningMatrix(cells=cells, notion_stats=notion_stats)

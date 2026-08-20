"""All three baselines emit the shared Violation format (Phase 7, mandated)."""

from __future__ import annotations

import re
from pathlib import Path

from auditors_trace.constraints.ruleset import CONSTRAINT_ID_PATTERN
from auditors_trace.constraints.templates import Violation, violation_sort_key
from auditors_trace.eval.metrics import confusion, session_match
from auditors_trace.model.log import OCELLog
from auditors_trace.scenario.injector import GroundTruthViolation

REPO = Path(__file__).resolve().parent.parent.parent


def test_baselines_emit_comparable_violation_format(mini_log: OCELLog, tmp_path: Path) -> None:
    """The mandated cross-system contract: judge, flattened DECLARE, and
    OC-DFG all produce canonical `Violation` lists that feed `confusion`
    with the session matcher."""
    import dataclasses

    from auditors_trace.baselines.declare_flat import (
        detect as declare_detect,
    )
    from auditors_trace.baselines.declare_flat import (
        discover_reference as declare_reference,
    )
    from auditors_trace.baselines.llm_judge import (
        ScriptedJudgeClient,
        judge,
        load_judge_prompts,
    )
    from auditors_trace.baselines.ocdfg import detect as ocdfg_detect
    from auditors_trace.baselines.ocdfg import discover_reference as ocdfg_reference
    from auditors_trace.constraints.ruleset import load_ruleset

    # A faulted variant every system can look at: session A's approvals gone.
    faulted_events = tuple(
        dataclasses.replace(
            event,
            relations=tuple(r for r in event.relations if r.object_id != "APRV-A"),
        )
        if event.event_id == "EV-A-09"
        else event
        for event in mini_log.events
        if event.event_id not in ("EV-A-07", "EV-A-08")
    )
    faulted = OCELLog(
        events=faulted_events,
        objects=tuple(obj for obj in mini_log.objects if obj.object_id != "APRV-A"),
        o2o=mini_log.o2o,
    )

    def one_claim(request: object) -> str:
        return (
            '{"violations": [{"constraint_id": "T1.synchronised_approval", '
            '"event_ids": ["EV-A-09"], "explanation": "format probe"}]}'
        )

    judge_run = judge(
        model_id="claude-haiku-4-5",
        sample_index=0,
        condition="ocel",
        session_id="SES-A",
        payload="test payload",
        candidate_event_ids=("EV-A-09",),
        prompts=load_judge_prompts(REPO / "rules" / "judge_prompts.yaml"),
        ruleset=load_ruleset(REPO / "rules" / "rules.yaml"),
        cache_dir=tmp_path,
        client=ScriptedJudgeClient(one_claim),  # type: ignore[arg-type]
    )
    outputs: dict[str, list[Violation]] = {
        "judge": list(judge_run.violations),
        "declare": declare_detect(
            faulted,
            case_notion="Application",
            reference=declare_reference(mini_log, "Application"),
        ),
        "ocdfg": ocdfg_detect(faulted, reference=ocdfg_reference(mini_log)),
    }

    truth = (
        GroundTruthViolation(
            violation_id="0" * 16,
            fault_class="V1",
            constraint_id="T1.synchronised_approval",
            session_id="SES-A",
            ocel_event_ids=("EV-A-09",),
            ocel_object_ids=("DEC-A",),
            expected_evidence_span_ids=(),
            removed_span_ids=(),
            severity="high",
            articles=("Reg (EU) 2024/1689 Art. 14",),
            description="constructed",
        ),
    )
    for name, violations in outputs.items():
        assert violations, name  # every system produced SOMETHING here
        for violation in violations:
            assert isinstance(violation, Violation), name
            assert re.match(CONSTRAINT_ID_PATTERN, violation.constraint_id), name
            assert violation.ocel_event_ids, name
        assert violations == sorted(violations, key=violation_sort_key), name
        matrix = confusion(violations, truth, matcher=session_match)
        assert matrix.true_positives + matrix.false_positives == len(violations), name

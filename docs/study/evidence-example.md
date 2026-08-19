# Evidence record example

> Provenance: the `single` evaluation split (seed 42); crosswalk content from the TEST FIXTURE tests/fixtures/crosswalk_fixture.yaml pending the human-authored rules/crosswalk.yaml

## What this record claims

**STD.policy_version_current** — Every decision must be governed by the policy version in force at decision time; a decision governed by a superseded policy version (one whose validity has ended) violates the constraint.

Severity **medium**. The record cites the exact events and
telemetry spans the finding rests on, the legal basis it traces to, and a
rerun command: executing

```
python -m auditors_trace.cli check --log 0e9fd533578f8e49c15837ec95171cd007e884211ce062e983a4e07a86f878aa --rules 2026-08.1
```

over the pinned artefacts re-derives this record byte-for-byte. Records are
hash-chained and tamper-evident: any change to any field breaks verification.

## The evidence record

```json
{
  "constraint": {
    "formal": "forall make_decision event d, forall PolicyVersion p with d governed_by p: effective_to(p) = \"\" (the policy in force has an open validity end; a non-empty effective_to marks a superseded version).",
    "id": "STD.policy_version_current",
    "natural_language": "Every decision must be governed by the policy version in force at decision time; a decision governed by a superseded policy version (one whose validity has ended) violates the constraint.",
    "ruleset_version": "2026-08.1"
  },
  "context": {
    "agent_versions": {
      "AGT-ADJUDICATION": "1.0.0",
      "AGT-INTAKE": "1.0.0",
      "AGT-RETRIEVAL": "1.0.0",
      "AGT-SCORING": "1.0.0"
    },
    "model_versions": {
      "scripted-credit-policy-v1": "1.0.0"
    },
    "policy_version": "2025.11"
  },
  "evidence": {
    "ocel_event_ids": [
      "EVT-SESS-0003-025-make_decision"
    ],
    "ocel_object_ids": [
      "POL-CREDIT-2025.11"
    ],
    "otel_span_ids": [
      "d9a6f477d3a9e586"
    ],
    "otel_trace_id": "ec2a84c8dff62876d1d8001514f2e82b"
  },
  "integrity": {
    "chain_index": 0,
    "prev_record_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "record_sha256": "b3f5a2aa067d2dfb57cc994fe20b267e25ed1f76e136273da5fd9e3f37a0aae4"
  },
  "legal_basis": [
    {
      "article": "72",
      "instrument": "Regulation (EU) 2024/1689",
      "paragraph": "1",
      "requirement": "Providers establish and document a post-market monitoring system proportionate to the nature of the AI technologies and the risks of the high-risk AI system."
    },
    {
      "article": "72",
      "instrument": "Regulation (EU) 2024/1689",
      "paragraph": "2",
      "requirement": "The post-market monitoring system actively and systematically collects and analyses relevant data on performance throughout the systems' lifetime, allowing the provider to evaluate continuous compliance with the requirements."
    }
  ],
  "provenance": {
    "engine_commit": "",
    "engine_version": "engine/0.1.0",
    "input_log_sha256": "0e9fd533578f8e49c15837ec95171cd007e884211ce062e983a4e07a86f878aa"
  },
  "reproducibility": {
    "rerun_command": "python -m auditors_trace.cli check --log 0e9fd533578f8e49c15837ec95171cd007e884211ce062e983a4e07a86f878aa --rules 2026-08.1"
  },
  "retention": {
    "class": "ai_act_art_26_6",
    "min_retention_months": 6,
    "note": "financial institutions: retain per applicable Union financial services law"
  },
  "severity": "medium",
  "standard_refs": [
    {
      "control": "A.8.4 change control for governing policies (fixture mapping)",
      "standard": "ISO/IEC 42001"
    }
  ],
  "violation_id": "d8ded48ce1779f7b"
}
```

## The session, as recorded

Session `SESS-0003` in full; the rows marked below are the events this
record cites as evidence.

| Event | Time (UTC) | Type | Key relations | Cited |
|---|---|---|---|---|
| `EVT-SESS-0003-001-session_start` | 2026-03-02T09:45:01Z | session_start | concerns -> APN-f76e3eaeef; concerns -> APP-d350ef068e; governed_by -> POL-CREDIT-2026.02 |  |
| `EVT-SESS-0003-002-invoke_agent` | 2026-03-02T09:45:02Z | invoke_agent | performed_by -> AGT-INTAKE; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-003-call_llm` | 2026-03-02T09:45:05Z | call_llm | performed_by -> AGT-INTAKE; concerns -> APP-d350ef068e; uses -> PRM-intake_summary-v1; uses -> scripted-credit-policy-v1 |  |
| `EVT-SESS-0003-004-emit_reasoning` | 2026-03-02T09:45:06Z | emit_reasoning | performed_by -> AGT-INTAKE; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-005-handoff` | 2026-03-02T09:45:07Z | handoff | from -> AGT-INTAKE; to -> AGT-RETRIEVAL; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-006-invoke_agent` | 2026-03-02T09:45:08Z | invoke_agent | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-007-call_tool` | 2026-03-02T09:45:10Z | call_tool | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e; uses -> TOOL-credit_bureau_lookup |  |
| `EVT-SESS-0003-008-retrieve_data` | 2026-03-02T09:45:12Z | retrieve_data | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e; reads -> RES-CREDIT-BUREAU |  |
| `EVT-SESS-0003-009-call_tool` | 2026-03-02T09:45:14Z | call_tool | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e; uses -> TOOL-income_verification |  |
| `EVT-SESS-0003-010-retrieve_data` | 2026-03-02T09:45:16Z | retrieve_data | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e; reads -> RES-INCOME |  |
| `EVT-SESS-0003-011-call_tool` | 2026-03-02T09:45:18Z | call_tool | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e; uses -> TOOL-internal_credit_history |  |
| `EVT-SESS-0003-012-retrieve_data` | 2026-03-02T09:45:20Z | retrieve_data | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e; reads -> RES-INTERNAL-HISTORY |  |
| `EVT-SESS-0003-013-call_tool` | 2026-03-02T09:45:22Z | call_tool | performed_by -> AGT-RETRIEVAL; concerns -> APP-d350ef068e; uses -> TOOL-demographic_bias_monitor |  |
| `EVT-SESS-0003-014-retrieve_data` | 2026-03-02T09:45:24Z | retrieve_data | performed_by -> AGT-RETRIEVAL; concerns -> APN-f76e3eaeef; concerns -> APP-d350ef068e; reads -> RES-DEMOGRAPHICS |  |
| `EVT-SESS-0003-015-handoff` | 2026-03-02T09:45:25Z | handoff | from -> AGT-RETRIEVAL; to -> AGT-SCORING; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-016-invoke_agent` | 2026-03-02T09:45:26Z | invoke_agent | performed_by -> AGT-SCORING; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-017-call_tool` | 2026-03-02T09:45:28Z | call_tool | performed_by -> AGT-SCORING; concerns -> APP-d350ef068e; uses -> TOOL-risk_scorecard |  |
| `EVT-SESS-0003-018-call_llm` | 2026-03-02T09:45:31Z | call_llm | performed_by -> AGT-SCORING; concerns -> APP-d350ef068e; uses -> PRM-score_rationale-v1; uses -> scripted-credit-policy-v1 |  |
| `EVT-SESS-0003-019-emit_reasoning` | 2026-03-02T09:45:32Z | emit_reasoning | performed_by -> AGT-SCORING; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-020-handoff` | 2026-03-02T09:45:33Z | handoff | to -> AGT-ADJUDICATION; from -> AGT-SCORING; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-021-invoke_agent` | 2026-03-02T09:45:34Z | invoke_agent | performed_by -> AGT-ADJUDICATION; concerns -> APP-d350ef068e |  |
| `EVT-SESS-0003-022-call_llm` | 2026-03-02T09:45:37Z | call_llm | performed_by -> AGT-ADJUDICATION; concerns -> APP-d350ef068e; uses -> PRM-adverse_action-v1; uses -> scripted-credit-policy-v1 |  |
| `EVT-SESS-0003-023-request_approval` | 2026-03-02T09:45:42Z | request_approval | performed_by -> AGT-ADJUDICATION; concerns -> APP-d350ef068e; concerns -> APR-001; requests -> APRV-SESS-0003; concerns -> DEC-APP-d350ef068e |  |
| `EVT-SESS-0003-024-deny_approval` | 2026-03-02T09:47:42Z | deny_approval | concerns -> APP-d350ef068e; performed_by -> APR-001; produces -> APRV-SESS-0003; concerns -> DEC-APP-d350ef068e |  |
| `EVT-SESS-0003-025-make_decision` | 2026-03-02T09:47:44Z | make_decision | performed_by -> AGT-ADJUDICATION; concerns -> APN-f76e3eaeef; concerns -> APP-d350ef068e; concerns -> APRV-SESS-0003; produces -> DEC-APP-d350ef068e; governed_by -> POL-CREDIT-2025.11 | **<-- cited** |
| `EVT-SESS-0003-026-emit_reasoning` | 2026-03-02T09:47:45Z | emit_reasoning | performed_by -> AGT-ADJUDICATION; concerns -> APP-d350ef068e; explains -> DEC-APP-d350ef068e |  |
| `EVT-SESS-0003-027-notify_applicant` | 2026-03-02T09:47:46Z | notify_applicant | performed_by -> AGT-ADJUDICATION; concerns -> APN-f76e3eaeef; concerns -> APP-d350ef068e; concerns -> DEC-APP-d350ef068e |  |
| `EVT-SESS-0003-028-session_end` | 2026-03-02T09:47:47Z | session_end | concerns -> APP-d350ef068e; concerns -> DEC-APP-d350ef068e |  |

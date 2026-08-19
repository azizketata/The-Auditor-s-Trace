# The Auditor's Trace — Build Plan

**Target venue:** ICPM 2027 (abstract 4 Sep 2026, full paper 11 Sep 2026 — verified official dates; conference 8–12 Feb 2027, Univ. of Calabria; ACM single-column format, max 13 pages incl. references)
**Authors:** Aziz Ketata (technical), Alina Hafner (regulatory, evaluation design)
**Document purpose:** a build specification written to be executed by an agentic coding harness (Claude Code or equivalent) with a human reviewing at each gate.

> **Amended 13 Aug 2026** after an evidence-based deep review (facts verified
> against primary sources; hostile methodology critique). The findings and
> rationale live in `docs/PLAN-REVIEW.md`; the amendments are folded into the
> sections below and marked *[A#/B# — amended]*. Key changes: span-level
> violation injection (B1), de-circularised detection evaluation with an
> expert-authored held-out fault set (B2), restructured determinism claim
> (B4), modernised LLM-judge spec (B5), inferential statistics (B6),
> per-flattening expressiveness matrix (B7), corrected legal anchors
> (A5: Art. 10(2)(f)–(g), not 10(5)), externally timestamped pre-registration
> (B9), and the expert-study protocol in `docs/EXPERT-STUDY-PROTOCOL.md`.

---

## 0. How to use this document

Each phase below is a self-contained unit of work with:

- **Goal** — one sentence
- **Deliverables** — the files that must exist when the phase is done
- **Implementation notes** — the decisions already made, so the harness does not re-litigate them
- **Acceptance tests** — named tests that must pass
- **Definition of done** — the gate

**Rules for the harness:**

1. Do not start a phase until the previous phase's DoD is met.
2. Write the test first where the acceptance test is named. Red, then green.
3. Never introduce a dependency not listed in section 4 without asking.
4. Never put an LLM call in the critical path of the constraint engine or the evidence renderer. Determinism is the paper's headline claim; an LLM anywhere in that path invalidates it.
5. After each phase, run `make check` and report which acceptance tests pass.
6. If a phase cannot be completed as specified, stop and report rather than substituting an approach.

---

## 1. The brief

Banks and insurers are putting LLM agents into credit scoring. Those systems are classified high-risk under Annex III point 5(b) of the EU AI Act, and the high-risk obligations apply from 2 December 2027 (Regulation (EU) 2026/1744 amending 2024/1689 — fixed date, verified; note 5(b)'s sole carve-out is financial fraud detection, so the scenario must remain creditworthiness evaluation). Article 12(1)–(2) requires logging sufficient to reconstruct decisions after the fact (12(3)'s minimum log content is biometric-only — never cite it here); Article 26(6) requires deployers to retain those logs for at least six months; Article 72 requires post-market monitoring fed by them. *[A1–A4 — amended]*

Nothing today produces that. Observability tooling produces dashboards. LLM-as-judge evaluation produces non-reproducible opinions.

**We build a pipeline that turns agent telemetry into deterministic, human-inspectable, article-level audit evidence.**

```
instrumented agent fleet
        │  OpenTelemetry / OpenInference spans
        ▼
   span → OCEL 2.0 mapper                    [BUILD]
        │  OCEL 2.0 log (objects + qualified relations)
        ▼
   object-centric constraint engine          [BUILD]
        │  violation set
        ▼
   evidence renderer                         [BUILD]
        │  hash-chained, tamper-evident evidence records
        ▼
   retention store + evaluation harness      [BUILD]
```

**Claims the code must support:**

| ID | Claim | Proven by |
|----|-------|-----------|
| C1 | OCEL 2.0 represents agent fleets in ways flat XES cannot | E1 expressiveness |
| C2 | The span-to-OCEL mapping is lossless for governance attributes | E2 ingestion fidelity |
| C3 | Object-centric declarative constraints beat LLM-as-judge on detection and are perfectly deterministic | E3 detection |
| C4 | Each violation renders into a defensible, reproducible evidence record | E4 evidence + inspectability |

---

## 2. Non-negotiable invariants

These are tested continuously, not at the end.

**I1 — Bitwise determinism.** Running the pipeline twice on the same input produces byte-identical evidence records. Enforced by: canonical JSON serialisation (sorted keys, fixed separators, no floats where avoidable), sorted iteration over all collections, no `set` iteration without sorting, no wall-clock timestamps inside the hashed payload, pinned dependency versions.

**I2 — No LLM in the critical path.** LLMs appear in exactly two places: inside the simulated agent fleet (the subject under test), and inside the LLM-as-judge baseline (the comparison). Nowhere else.

**I3 — Every violation traces to a legal requirement.** No constraint exists in the ruleset without an article reference. Enforced by schema validation on the ruleset file.

**I4 — Ground truth is pre-registered.** The violation catalogue, `rules.yaml`, the template parameters, the verbatim judge prompts, and the judge-to-ground-truth matching function are frozen in the *same* tagged commit before any detection experiment runs. The tag is pushed to the public remote and its hash externally timestamped (Zenodo/OSF/RFC 3161) — a local git tag alone proves nothing. *[B9 — amended]*

**I5 — Every artefact is regenerable.** `make all` reproduces every number and figure in the paper from scratch.

---

## 3. Repository layout

```
auditors-trace/
├── CLAUDE.md                      # harness instructions, see §13
├── Makefile
├── pyproject.toml
├── README.md
├── src/auditors_trace/
│   ├── __init__.py
│   ├── model/
│   │   ├── ocel_schema.py         # object/event type definitions
│   │   ├── log.py                 # in-memory OCEL log structure
│   │   └── io.py                  # read/write OCEL 2.0 via pm4py
│   ├── ingest/
│   │   ├── otel_reader.py         # OTLP / OpenInference span reader
│   │   ├── mapper.py              # span tree → OCEL events + objects
│   │   └── attribute_map.py       # gen_ai.* / openinference.* → OCEL attrs
│   ├── constraints/
│   │   ├── templates.py           # the 5 agent-specific templates
│   │   ├── standard.py            # object-scoped response/precedence/absence
│   │   ├── engine.py              # evaluation over an OCEL log
│   │   └── ruleset.py             # load/validate rules.yaml
│   ├── evidence/
│   │   ├── record.py              # evidence record dataclass + schema
│   │   ├── renderer.py            # violation → evidence record
│   │   ├── chain.py               # hash chaining
│   │   └── crosswalk.py           # article + standard reference table
│   ├── scenario/
│   │   ├── agents.py              # the credit-scoring agent fleet
│   │   ├── seed.py                # German Credit loader
│   │   └── injector.py            # violation injection
│   ├── baselines/
│   │   ├── llm_judge.py
│   │   ├── declare_flat.py        # pm4py case-centric DECLARE
│   │   └── ocdfg.py               # pm4py OC-DFG conformance
│   └── eval/
│       ├── metrics.py
│       ├── runner.py
│       └── figures.py
├── rules/
│   ├── rules.yaml                 # the constraint ruleset
│   └── crosswalk.yaml             # article/standard mapping
├── data/
│   ├── seed/                      # German Credit (downloaded, not committed)
│   ├── generated/                 # spans, OCEL logs (gitignored)
│   └── catalogue/
│       └── violations.yaml        # PRE-REGISTERED ground truth
├── tests/
│   ├── unit/
│   ├── golden/                    # golden files for mapper + renderer
│   ├── integration/
│   └── determinism/
├── results/                       # metrics, tables, figures (gitignored)
└── paper/
    └── artefacts/                 # final tables + figures for the paper
```

---

## 4. Dependency policy

**Allowed, pinned in `pyproject.toml`:**

| Package | Purpose | Notes |
|---------|---------|-------|
| `pm4py` | OCEL 2.0 read/write, baseline conformance | pin exact version |
| `pydantic` | schema validation for records and rulesets | v2 |
| `pyyaml` | rulesets, catalogue | |
| `opentelemetry-sdk` | span capture | |
| `openinference-instrumentation-langchain` | agent instrumentation | |
| `langgraph`, `langchain` | agent fleet | scenario only |
| `pytest`, `pytest-cov`, `hypothesis` | testing | |
| `pandas` | evaluation tables only | never in the deterministic path |
| `matplotlib` | figures | |

**Explicitly NOT used:**

- `rust4pm` / OC-DECLARE reference implementation. **Decision: we implement the constraint templates ourselves.** Reason: the license is unverified, the templates we need are simple synchronisation predicates, and owning the engine is what makes determinism auditable. We cite OC-DECLARE (Küsters and van der Aalst, BPM 2025) as the conceptual origin of object-synchronised declarative constraints and position ours as an agent-specific instantiation.
- Any AI governance SaaS or vendor SDK.
- Any package that is not deterministic across runs.

---

## 5. The OCEL 2.0 model (spec, build to this exactly)

### Object types

| Type | Key attributes |
|------|----------------|
| `Application` | application_id, product_type, amount, submitted_at |
| `Applicant` | applicant_id (pseudonymous), jurisdiction |
| `Agent` | agent_id, name, role, version, framework |
| `CreditDecision` | decision_id, outcome (grant/deny/refer), score, reason_codes |
| `Approval` | approval_id, approver_role, granted (bool) |
| `HumanApprover` | approver_id, role, authority_level |
| `Prompt` | prompt_name, prompt_version, template_hash |
| `Model` | model_id, provider, version |
| `Tool` | tool_name, tool_type |
| `DataResource` | resource_id, classification (public/internal/special_category), lawful_basis |
| `PolicyVersion` | policy_id, version, effective_from, effective_to |
| `Session` | session_id, workflow_name |

### Event types

`session_start`, `invoke_agent`, `handoff`, `call_llm`, `call_tool`, `retrieve_data`, `emit_reasoning`, `request_approval`, `grant_approval`, `deny_approval`, `make_decision`, `notify_applicant`, `session_end`

### Qualified relations (event to object)

Examples the mapper must produce:

- `make_decision` → `CreditDecision` [produces], → `Application` [concerns], → `Applicant` [concerns], → `PolicyVersion` [governed_by], → `Agent` [performed_by]
- `retrieve_data` → `DataResource` [reads], → `Application` [concerns], → `Agent` [performed_by]
- `grant_approval` → `Approval` [produces], → `HumanApprover` [performed_by], → `CreditDecision` [approves]
- `handoff` → `Agent` [from], → `Agent` [to], → `Session` [within]

### O2O relations

`Agent` --delegates_to--> `Agent`, `CreditDecision` --derived_from--> `Application`, `Application` --submitted_by--> `Applicant`

---

## 6. The constraint templates (spec)

Each template is a function `(log, params) -> list[Violation]`. All iteration is sorted. All templates are pure.

**T1 SynchronisedApproval**
> For every `make_decision` event `e` producing decision `d` and concerning applicant `a`, there must exist a `grant_approval` event `e'` such that `e'` approves the same `d`, is performed by a `HumanApprover` whose role is in `allowed_roles`, and `e'.timestamp < e.timestamp`.

Params: `allowed_roles`, `decision_outcomes` (which outcomes require approval).

**T2 MandatoryDataCoverage**
> For every `CreditDecision` `d`, the set of `DataResource` objects read by `retrieve_data` events concerning the same `Application` must cover every resource type in `required_sources`.

Params: `required_sources`.

**T3 DelegationIntegrity**
> Every event performed by agent `B` within session `s` must be preceded in `s` either by a `handoff` event with `to = B`, or by `B` being the session entrypoint. No `handoff` chain within a session may contain a cycle.

Params: `entrypoint_roles`.

**T4 ReasonCodePresence**
> Every `make_decision` producing a `CreditDecision` with outcome in `adverse_outcomes` must have non-empty `reason_codes` on that decision object, and at least one `emit_reasoning` event referencing the same decision.

Params: `adverse_outcomes`.

**T5 ProhibitedAttributeAccess**
> No `retrieve_data` or `call_llm` event concerning an `Application` may reference a `DataResource` whose `classification` is `special_category` unless that resource carries a non-empty `lawful_basis`.

Params: `prohibited_classifications`.

**Standard object-scoped templates** (in `standard.py`): `ObjectExistence`, `ObjectAbsence`, `SynchronisedResponse`, `SynchronisedPrecedence`. These are the object-scoped analogues of classic DECLARE and are needed for the E1 comparison against case-centric DECLARE.

---

## 7. The violation catalogue (pre-register before Phase 4)

`data/catalogue/violations.yaml`. Frozen and git-tagged before any detection run.

| ID | Injected fault | Detected by | Article |
|----|----------------|-------------|---------|
| V1 | Decision made with no approval event | T1 | Art. 14, Art. 26(2) |
| V2 | Approval exists but references a different decision | T1 | Art. 14 |
| V3 | Approval granted by an unauthorised role | T1 | Art. 14, Art. 26(2) |
| V4 | A mandatory data source is never retrieved | T2 | Art. 10, Art. 15 |
| V5 | Protected-attribute resource read without documented bias-examination basis | T5 | Art. 10(2)(f)–(g) *[A5 — amended: 10(5) refuted as basis for non-Art.-9 attributes]* |
| V6 | Deny decision with empty reason codes | T4 | Art. 13, Art. 86 |
| V7 | Agent acts without a preceding handoff | T3 | Art. 14, Art. 26 |
| V8 | Decision governed by a superseded PolicyVersion | T-standard | Art. 72 |

Each catalogue entry records: fault id, injection function, expected violating event ids, expected constraint id, expected severity.

**Amendments (13 Aug 2026, see docs/PLAN-REVIEW.md B1/B2/B11):**

- **Injection happens at SPAN level**, not on the OCEL log: a pure seeded
  function over the span JSONL files, re-mapped through the Phase 3 mapper —
  so every system (engine, judge, flattened DECLARE, OC-DFG) consumes
  artifacts derived from the same faulted telemetry, and evidence records
  cite span ids that actually exhibit the fault. (The original OCEL-level
  design made the judge structurally blind to the faults.)
- **V1–V8 recall on the `single` split is verification, not a result.** The
  reported generalization numbers come from: (i) a **held-out fault set**
  (4–8 variants authored by Alina + the recruited practitioner from the
  article text and the realism-review elicitation, sealed until the template
  freeze — firewall procedure in docs/EXPERT-STUDY-PROTOCOL.md §7a);
  (ii) surface-form perturbations of each injection (roles, orderings,
  timestamps, session shapes); (iii) a near-miss compliant distractor suite
  (approval at the authority boundary, refer without approval,
  protected-attribute read WITH documented basis) probing false positives
  beyond the trivially clean split.
- **Mixed-split composition rules are explicit:** co-injectable fault pairs
  are declared in a compatibility matrix (or injection is sequential with
  ground truth recomputed per step); acceptance test
  `test_composed_injections_have_consistent_ground_truth`.

---

## 8. The evidence record (schema)

```python
{
  "violation_id": "<sha256 of canonical payload, first 16 hex>",
  "constraint": {
    "id": "T1.synchronised_approval",
    "natural_language": "...",
    "formal": "...",
    "ruleset_version": "..."
  },
  "legal_basis": [
    {"instrument": "Regulation (EU) 2024/1689",
     "article": "14", "paragraph": "4(d)",
     "requirement": "..."}
  ],
  "standard_refs": [
    {"standard": "ISO/IEC 24970", "clause": "..."},
    {"standard": "ISO/IEC 42001", "control": "..."},
    {"framework": "NIST AI RMF", "function": "MEASURE"}
  ],
  "severity": "high",
  "evidence": {
    "ocel_event_ids": ["..."],
    "ocel_object_ids": ["..."],
    "otel_trace_id": "...",
    "otel_span_ids": ["..."]
  },
  "context": {
    "policy_version": "...",
    "model_versions": {"...": "..."},
    "agent_versions": {"...": "..."}
  },
  "provenance": {
    "engine_version": "...",
    "engine_commit": "...",
    "input_log_sha256": "..."
  },
  "reproducibility": {
    "rerun_command": "python -m auditors_trace.cli check --log <hash> --rules <version>"
  },
  "integrity": {
    "chain_index": 0,
    "record_sha256": "...",
    "prev_record_sha256": "..."
  },
  "retention": {
    "class": "ai_act_art_26_6",
    "min_retention_months": 6,
    "note": "financial institutions: retain per applicable Union financial services law"
  }
}
```

**Hashing rule:** `record_sha256` is computed over the canonical JSON of every field except `integrity`. `violation_id` is computed over `constraint.id + sorted(evidence.ocel_event_ids) + input_log_sha256`. No wall-clock value enters any hash.

*[B12 — amended: the original schema placed `expected_record_sha256` inside
`reproducibility`, i.e. inside its own hashed payload — a fixed-point
impossibility. The record's own hash lives only in `integrity` (excluded from
hashing); `rerun_command` alone carries the reproducibility semantics.
Records are hash-chained and tamper-evident; the word "signed" is not used
unless Phase 6 implements an actual signature.]*

---

## 9. Phase plan

### Phase 0 — Scaffold

**Goal.** A repository that runs an empty test suite and a `make check` that passes.

**Deliverables.** `pyproject.toml`, `Makefile`, `CLAUDE.md`, package skeleton with all modules present as stubs, `.gitignore`, CI config.

**Implementation notes.** `make check` runs: `ruff`, `mypy --strict` on `src/`, `pytest`. Pin every dependency to an exact version. Python 3.11.

**Acceptance tests.**
- `make check` exits 0
- `pytest --collect-only` finds the test directories

**DoD.** Clean run on a fresh clone.

---

### Phase 1 — OCEL model and I/O

**Goal.** Represent an agent-fleet log in memory and round-trip it through OCEL 2.0 on disk.

**Deliverables.** `model/ocel_schema.py`, `model/log.py`, `model/io.py`, a hand-written miniature fixture log at `tests/fixtures/mini_log.json`.

**Implementation notes.** Define object and event types exactly as section 5. `log.py` holds sorted, immutable collections. `io.py` wraps pm4py's OCEL 2.0 readers and writers for all three serialisations (XML, JSON, SQLite). Compute a canonical log hash: sha256 over canonical JSON of the sorted log content, excluding file metadata.

*Gate spike result (19 Aug 2026, docs/SPIKE-pm4py-roundtrip.md; pinned in `tests/integration/test_pm4py_roundtrip.py`):* qualified relations **survive** all three serialisations — gate passed, no §12 fallback. Three pm4py behaviors constrain the model: (a) exporters silently delete objects with zero E2O relations plus any O2O edge touching them, so every pure declaration materialises as a `declares`-qualified E2O relation from its declaring event (new OCEL-level `Qualifier.DECLARES`, never emitted in `at.*` spans — contract `at-span/1` unchanged); (b) the JSON importer deduplicates E2O by (event, object) pair, so `OCELLog` enforces at most one relation per pair; (c) the JSON exporter truncates timestamps to whole seconds, which the simulated clock already emits. Round-trip "preserves" therefore means model → write → read → model equality; the log hash is computed over canonical JSON of the model, never over file bytes.

**Acceptance tests.**
- `test_roundtrip_json_preserves_log`
- `test_roundtrip_xml_preserves_log`
- `test_roundtrip_sqlite_preserves_log`
- `test_log_hash_is_stable_across_runs`
- `test_log_hash_changes_when_any_attribute_changes`
- `test_qualified_relations_survive_roundtrip` (the one most likely to break; pm4py's qualifier support must be verified, not assumed)

**DoD.** All six pass. If qualified relations do not survive a pm4py round trip, stop and report before proceeding, because the whole model depends on them.

---

### Phase 2 — Scenario: the credit-scoring agent fleet

**Goal.** A runnable multi-agent credit workflow that emits real OpenTelemetry spans.

**Deliverables.** `scenario/agents.py`, `scenario/seed.py`, a CLI entrypoint `python -m auditors_trace.scenario run --n 50 --seed 42`.

**Implementation notes.** Four agents: `intake`, `data_retrieval`, `scoring`, `adjudication`, plus a simulated human approval step. Seed applicant records from the Statlog German Credit dataset (UCI id 144, CC BY 4.0), downloaded at build time into `data/seed/`, never committed. Instrument with OpenInference. Set temperature 0 and a fixed seed on every LLM call, and record both. Write raw spans to `data/generated/spans/`.

The fleet must be re-skinnable: keep the agent graph declarative so a second scenario can be swapped in without touching the mapper.

**Acceptance tests.**
- `test_scenario_runs_end_to_end`
- `test_spans_contain_required_genai_attributes` (asserts presence of `gen_ai.request.model`, `gen_ai.tool.name`, `gen_ai.conversation.id` or their OpenInference equivalents)
- `test_span_tree_is_well_formed` (every non-root span has a resolvable parent)
- `test_same_seed_produces_same_applicant_sequence`

**DoD.** 50 sessions generated, spans on disk, tests pass. Note that LLM output itself will not be bitwise reproducible; that is fine and expected, because determinism is required of the *analysis* pipeline, not the subject under test. Record this explicitly in the README.

---

### Phase 3 — Span to OCEL mapper

**Goal.** Convert a span tree into an OCEL 2.0 log with correct objects, events and qualified relations.

**Deliverables.** `ingest/otel_reader.py`, `ingest/mapper.py`, `ingest/attribute_map.py`, golden files under `tests/golden/mapper/`.

**Implementation notes.** Walk the span tree depth-first in deterministic order (sort children by start time, then span id). Use span kind (`openinference.span.kind` or the OTel GenAI equivalent) to select the event type. Support both the `gen_ai.*` and `openinference.*` / `llm.*` vocabularies, since the conventions are still experimental and instrumentation varies. Missing attributes must degrade gracefully and be recorded in a coverage report, never silently dropped.

Object identity: derive stable object ids from span attributes, not from generation order.

*[C1 — amended 19 Aug 2026]* The span-kind heuristic above is superseded by the `at-span/1` layering rule (`model/span_contract.py`, authoritative): a span is an OCEL event **iff** it carries `at.event.type`; event order comes from `at.event.seq` and timestamps from `at.event.time` (simulated clock, never span nanos). Span kind classifies layer-B enrichment for the coverage report only. Two further build facts: the mapper emits a **span-index sidecar** (`<out>.span_index.json`) because §8 evidence records cite `otel_trace_id`/`otel_span_ids` and the frozen OCEL attribute vocabulary cannot carry span ids; and the DoD's `results/e2_coverage.json` is committed via an explicit `.gitignore` exception (§3 gitignores `results/*`), regenerated byte-identically by `make scenario && make ingest`.

**Acceptance tests.**
- `test_mapper_golden_small` (fixed span file in, golden OCEL out, byte-identical)
- `test_mapper_is_deterministic` (run 10 times, identical log hash)
- `test_attribute_coverage_report` (reports percentage of governance-relevant attributes mapped; must be above 0.9 on the Phase 2 output)
- `test_unknown_attributes_are_recorded_not_dropped`
- `test_handoff_spans_produce_agent_to_agent_o2o`
- `test_both_vocabularies_map_to_same_ocel` (a span in `gen_ai.*` form and the same span in `openinference.*` form produce identical OCEL)

**DoD.** All pass, and the coverage report is committed as `results/e2_coverage.json`. This satisfies claim C2.

---

### Phase 4 — Violation injector and catalogue

**Goal.** Produce labelled logs with known, injected governance violations.

**Deliverables.** `scenario/injector.py`, `data/catalogue/violations.yaml`, a git tag `catalogue-v1`.

**Implementation notes.** *[B1 — amended]* Injection operates at **span
level**: each injector is a pure function `(span_files, rng_seed) ->
(span_files, list[GroundTruthViolation])`, equally exact and reproducible,
after which the faulted spans are re-mapped through the Phase 3 mapper to
produce labelled OCEL logs. This keeps every downstream system (engine,
judge, baselines) consuming artifacts derived from the same faulted
telemetry, and exercises the mapper under fault conditions (strengthening
C2). Generate three splits: `clean` (no injections, for false-positive
measurement), `single` (one violation per session), `mixed` (zero to three
per session, composition rules per §7). Stratify injections so every
violation class reaches ≥30 instances across `single`+`mixed` (≈300+
sessions; cheap for deterministic systems — the judge may be evaluated on a
stratified subsample with CIs). *[B6]*

**Critical:** freeze per invariant I4 (same tagged commit: catalogue,
rules.yaml, template parameters, judge prompts, matching function; pushed +
externally timestamped) before running any detection. The realism review runs
as a **structured expert content-validity review** per
docs/EXPERT-STUDY-PROTOCOL.md Study A — documented instrument, consent,
recorded in `data/catalogue/REVIEW.md` — not an informal read-through. Its
"what's missing?" elicitation feeds the held-out fault set (§7), under the
§7a firewall: Aziz is absent for the elicitation and sealed from the fault
specs until the template freeze. This is the cheapest available insurance
against the project's biggest risk.

**Acceptance tests.**
- `test_each_catalogue_entry_has_article_reference`
- `test_injector_is_deterministic_given_seed`
- `test_injected_violation_ids_are_recoverable_from_log`
- `test_clean_split_contains_zero_injected_violations`
- `test_catalogue_hash_matches_tag`
- `test_composed_injections_have_consistent_ground_truth` *[B11]*

*[C2 — amended 19 Aug 2026]* Violation ids are content-addressed **without**
the mapped log's hash: `sha256` over `(fault_class, constraint_id,
session_id, sorted ocel_event_ids, sorted removed_span_ids, base_run_id,
injector_seed, violations_catalogue_sha256)`. This keeps the injector a pure
single-pass span-level function (the B1 signature), removes any
injector→mapper dependency, and means a mapper bugfix can never churn frozen
label ids; the ground-truth join key for matching remains
`(constraint_id, ocel_event_ids)`, which ride in `at.event.id` and are known
at injection time. Recoverability from the mapped log is asserted by test.

**DoD.** All pass, catalogue tagged, human review recorded in `data/catalogue/REVIEW.md`.

---

### Phase 5 — Constraint engine

**Goal.** Evaluate the five agent templates plus the standard object-scoped templates over an OCEL log.

**Deliverables.** `constraints/templates.py`, `constraints/standard.py`, `constraints/engine.py`, `constraints/ruleset.py`, `rules/rules.yaml`.

**Implementation notes.** Build the templates in the order T1, T4, T5, T2, T3. T1, T4 and T5 are local and simple; T2 needs set coverage; T3 needs graph reachability and cycle detection and is the hardest, so it goes last.

Every template returns `Violation` objects carrying the exact event and object ids involved. No template may reference wall-clock time. The engine sorts violations by (constraint_id, first event id) before returning.

`rules.yaml` is validated against a pydantic schema on load; a rule without an article reference is a load-time error (invariant I3).

**Acceptance tests.**

Per template, on hand-crafted mini logs:
- `test_t1_flags_missing_approval`
- `test_t1_flags_wrong_decision_approval`
- `test_t1_flags_unauthorised_role`
- `test_t1_passes_valid_approval`
- `test_t2_flags_missing_required_source`
- `test_t3_flags_orphan_agent_action`
- `test_t3_flags_delegation_cycle`
- `test_t4_flags_empty_reason_codes`
- `test_t5_flags_special_category_without_basis`
- `test_t5_passes_with_lawful_basis`

Engine level:
- `test_engine_is_deterministic` (100 runs, identical violation set and order)
- `test_engine_finds_all_injected_on_single_split` (recall 1.0 on the single split, the smoke test for the whole approach)
- `test_engine_zero_violations_on_clean_split` (precision check)
- `test_ruleset_without_article_fails_validation`

**DoD.** All pass. Recall on the `single` split must be 1.0; if it is not, the templates are wrong, not the metric.

*Phase 5 amendments (19 Aug 2026, recorded at implementation):*

1. *T5's `call_llm` arm is provably vacuous under the frozen section-5 qualifier
   matrix — no `(call_llm, DataResource)` pair exists, and the OCEL model
   rejects such a relation. Implemented defensively as specified and pinned by
   `test_t5_call_llm_arm_is_vacuous_under_the_frozen_matrix`.*
2. *`STD.policy_version_current` (V8) is realised as the attribute-predicated
   form of `standard.py::object_absence` (`violating_when: non_empty` on
   `PolicyVersion.effective_to`), anchored on `make_decision`'s `governed_by`
   only — the pre-registered V8 evidence anchor; `session_start` governance is
   out of scope by design.*
3. *`eval/metrics.py::confusion` / `precision_recall_f1` and the default
   `exact_match` predicate are pulled forward from Phase 8: invariant I4
   freezes the ground-truth matching function with the catalogue, and the
   recall gate needs the pinned predicate now. `confusion` takes a `matcher`
   keyword (default `exact_match` — exact set equality on
   `(constraint_id, event_ids)`); the judge's looser overlap matcher plugs
   into the same seam in Phase 7/8 without touching the default.*
4. *Injector fix surfaced by the recall gate: `fault_v2`'s eligibility gated on
   `has_grant_approval` alone, so 11/30 V2 labels landed in grant-then-refer
   sessions — ground truth the pre-registered T1 semantics deliberately cannot
   flag (refer requires no approval; that exclusion is the catalogue's own
   clean-split-safety rule). V2 now additionally requires outcome ∈
   {grant, deny}, exactly like V1/V3; splits regenerated. This is precisely the
   kind of injector/template coherence defect the pre-freeze recall
   verification exists to catch (B2).*

---

### Phase 6 — Evidence renderer

**Goal.** Turn each violation into a hash-chained evidence record conforming to section 8.

**Deliverables.** `evidence/record.py`, `evidence/renderer.py`, `evidence/chain.py`, `evidence/crosswalk.py`, `rules/crosswalk.yaml`, golden records under `tests/golden/evidence/`.

**Implementation notes.** Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Hash chain: records sorted deterministically, `prev_record_sha256` of the first record is 64 zeros. `generated_at` may exist as a field but must be excluded from every hash.

The crosswalk file is Alina's deliverable in content; the harness builds the loader and validator.

**Acceptance tests.**
- `test_record_matches_schema`
- `test_record_hash_is_stable_across_runs`
- `test_record_hash_excludes_generated_at`
- `test_chain_is_valid` (each record's prev hash equals the previous record's hash)
- `test_chain_detects_tampering` (mutate one record, verification fails)
- `test_every_record_has_at_least_one_legal_basis`
- `test_golden_evidence_bitwise_identical`
- `test_rerun_command_reproduces_record`

**DoD.** All pass, including the tamper test. This satisfies claim C4's technical half.

*Phase 6 amendments (19 Aug 2026, recorded at implementation — resolutions of
spec gaps the section 8/9-P6 text left open):*

1. *Chain semantics: records sort by (constraint.id, evidence.ocel_event_ids,
   evidence.ocel_object_ids) — identical to the engine's canonical violation
   order, so chain order equals `evaluate()` output order; `chain_index` is
   the 0-based contiguous list position; one chain per rendered log;
   verification RECOMPUTES every record's hash.*
2. *`violation_id` payload: canonical JSON of `{"constraint_id",
   "input_log_sha256", "ocel_event_ids": sorted}` (house dict style), sha256
   first 16 hex. Distinct by design from the injector's ground-truth ids
   (amendment C2); the two id spaces are never compared.*
3. *Source split: `constraint.natural_language`, `legal_basis[]` and
   `standard_refs[]` come from the crosswalk verbatim; `constraint.formal`
   and `ruleset_version` from the ruleset — `rules.yaml` gained a required,
   substance-validated `formal` field per rule (transcribed from the
   implemented template semantics; an I4 freeze-bundle item); `severity`
   comes from the rule, and the crosswalk is cross-validated against the
   ruleset (severity + legal-basis article sets must agree).*
4. *Crosswalk: fixture-only until the human-authored `rules/crosswalk.yaml`
   lands (§11 — the harness never generates its content). The loader takes
   the required constraint-id set explicitly, rejects placeholder text
   everywhere (the all-TODO template can never load), rejects duplicate YAML
   keys, and requires any retention block to equal section 8's constant.
   Goldens are re-cut against the real crosswalk pre-freeze.*
5. *`provenance.engine_commit` is an explicit render input (default "", which
   the goldens pin) — reading git at render time would be environment-bound
   and break golden byte-identity (I1).*
6. *Context derivation: a prebuilt render index (the renderer never touches
   the log) — `policy_version` from the `governed_by` PolicyVersion of the
   session's first `make_decision`; `model_versions`/`agent_versions` over
   the session's SEMANTIC relations only (`uses`, `performed_by`, `from`,
   `to`; `declares` never contributes). Violations without a session render
   empty context.*
7. *`evidence.otel_trace_id` stays singular: cited events spanning more than
   one trace raise a typed error (engine violations never do);
   `otel_span_ids` are the cited events' OWN spans in citation order, 1:1 —
   never enrichment spans (B1). Ground-truth agreement is subset-shaped:
   every cited span is pre-registered in `expected_evidence_span_ids`, which
   may pre-register manifestation sites beyond the anchor events (V3's
   request_approval span, V6-B's stripped emit_reasoning span).*
8. *`rerun_command` embeds the substituted values; `--log`/`--rules` are
   verification pins (mismatch = exit 4) and artifact paths are supplied at
   invocation. `cli check` exit codes: 0 ok / 2 usage / 3 input unavailable /
   4 verification mismatch / 6 crosswalk error / 7 model-ingest-render
   rejection. Output is raw bytes (stdout via the buffer; atomic file
   writes) so Windows newline translation can never break reproduction.*
9. *`Violation.session_id`/`detail` deliberately have no section 8 home; no
   `schema_version` field was added (section 8 exact). `generated_at` exists
   as an optional display field, structurally excluded from every hash.
   Stale pre-B12 wording fixed: the package is "hash-chained,
   tamper-evident", never "signed" (B3), and `reproducibility` carries only
   the rerun command.*
10. *Beyond §9-P6's deliverables (user-approved): a stdlib-only study-pack
    renderer (`evidence/pack.py` + `cli pack`) producing the committed
    `docs/study/evidence-example.md` — Study A's "rendered example evidence
    trace" and Study B's record-plus-log-excerpt; and a strict span-index
    reader (`ingest.mapper.read_span_index`) as the sidecar's way back.*

---

### Phase 7 — Baselines

**Goal.** Three comparison systems producing violation sets on the same logs.

**Deliverables.** `baselines/llm_judge.py`, `baselines/declare_flat.py`, `baselines/ocdfg.py`.

**Implementation notes.**

*LLM-as-judge:* *[B5 — amended; the original "3 models × 5 seeds, temperature
0" is not implementable on Aug-2026 frontier APIs and reads as a strawman.]*
Pin 3 current frontier models with exact version strings and access dates in
provenance. Run **5 repeated samples per model per input** — the measured
quantity is honestly the end-to-end run-to-run variability of the deployed
service, which is the auditor-relevant quantity (no vendor seed exists;
temperature is rejected by current frontier models — use provider defaults
and record them). Use each provider's **native structured-output mode**;
report schema-failure rates separately, not as the headline. Give the judge
the natural-language rules *with article references* and evaluate BOTH input
conditions separately: raw spans and the serialized OCEL log. Develop the
prompt only on a dev split disjoint from evaluation sessions; freeze it
verbatim in the I4 tagged commit. Add a **majority-vote-of-5** aggregated
judge as the strong variant (nearly free given caching). Report per-session
cost and latency for every system. Score judge claims against ground truth
ONLY via the pre-registered matching function (constraint-class synonym map +
event-id overlap threshold, session-level partial credit reported
separately); judge-flagged violations matching no catalogue entry are blindly
adjudicated against the article text by Alina + the practitioner before
counting as false positives. Cache every response keyed by (model, sample
index, input hash) so the evaluation replays without API access. Acknowledge
agentic tool-using judges as a plausible stronger baseline left to future
work.

*Case-centric DECLARE:* *[B7 — amended]* flatten the OCEL log to XES on
**every viable case notion** (`Application`, `Session`, `CreditDecision`,
`Agent`), run pm4py's declarative conformance on each, and report a
per-flattening × per-violation-class detectability matrix with event
duplication/loss counts (convergence/divergence statistics). The defensible
E1 claim is: *no single case notion covers all governance constraint classes,
and cross-object synchronisation (V2) is inexpressible without object
identity in any of them* — scoped to single-case-notion XES with case-centric
DECLARE; richer flat encodings conceded in threats to validity.

*OC-DFG:* pm4py's object-centric DFG conformance, as the non-declarative object-centric baseline.

**Acceptance tests.**
- `test_llm_judge_output_parses_or_reports_error`
- `test_llm_judge_is_cached_and_replayable`
- `test_llm_judge_variance_is_measured` (asserts the variance metric is computed, not that it is low)
- `test_flattening_loses_synchronisation_violations` (the key E1 result: at least V2 and V7 become undetectable after flattening)
- `test_baselines_emit_comparable_violation_format`

**DoD.** All pass, all LLM responses cached in `data/generated/judge_cache/`.

---

### Phase 8 — Evaluation harness

**Goal.** Produce every number in the paper with one command.

**Deliverables.** `eval/metrics.py`, `eval/runner.py`, `eval/figures.py`, `results/`, `paper/artefacts/`.

**Implementation notes.** Metrics:

- Detection *[B2/B6 — amended]*: precision, recall, F1 overall and per
  violation class on `single` and `mixed`; false-positive rate on `clean`
  (clean split verified by independent manual audit of a session sample, not
  by the engine); **held-out fault set recall reported separately as the
  generalization result**; perturbation and distractor results per class.
  Engine recall 1.0 on `single` is a verification requirement (a test), never
  a reported finding. All rates with Wilson/Clopper-Pearson CIs; aggregate F1
  with bootstrap-over-sessions CIs; paired system comparisons via McNemar's
  test. Acceptance test: `test_metrics_report_confidence_intervals`.
- Determinism *[B4 — amended]*, three layers: (1) by construction (no
  stochastic operations, sorted iteration, no wall-clock in hashes — enforced
  by tests, argued in the paper); (2) empirically corroborated by **bitwise
  hash equality of the full evidence bundle across 20 runs AND across
  platforms** (Windows + Linux CI matrix, pinned Python); (3) judge
  variability reported per model as exact-match rate over 5 samples, distinct
  verdict-set counts, per-item flip rates, with exact binomial CIs. Framing:
  determinism is a *requirement whose cost is measured* (what detection
  performance, if any, the deterministic approach gives up) — never a race
  the rule engine wins by definition; note flat-DECLARE and OC-DFG are
  equally deterministic, so the differentiator is determinism AND
  expressiveness jointly. Preempt the batch-invariant-inference
  counterargument: even a bit-deterministic hosted judge is not third-party
  reproducible evidence, because reproduction depends on a mutable service;
  the pipeline's rerun_command over pinned artifacts is.
- Evidence reproducibility: fraction of records with identical hashes across
  runs and across platforms
- Scalability *[B14 — amended]*: wall-clock runtime vs event count, log-log
  plot, sessions from 50 to 5000. Large logs are produced by deterministic
  replication/perturbation of the base log with fresh object ids (the fleet
  is not rerun at 5000); a test asserts replicated logs preserve per-session
  event-type distributions, and the plot is labelled *engine-runtime
  scaling* — no claim about scenario diversity at scale.
- Expressiveness *[B7 — amended]*: the per-flattening × per-violation-class
  detectability matrix from Phase 7 (an experiment summarised by a table, not
  a table in place of an experiment).

`make all` runs everything from raw seed data to final figures.

**Acceptance tests.**
- `test_metrics_on_known_confusion_matrix` (unit test of the metric functions against hand-computed values)
- `test_runner_reproduces_committed_results` (rerun, compare to `results/expected.json`)
- `test_figures_generate_without_error`
- `test_make_all_is_idempotent`

**DoD.** `make all` from a clean checkout reproduces `results/expected.json` exactly. This satisfies invariant I5 and claim C3.

---

### Phase 9 — Packaging for submission

**Goal.** An artefact a reviewer can run.

**Deliverables.** README with a five-minute quickstart, a Zenodo-ready archive, the OCEL logs published as a dataset, a `REPRODUCE.md`.

**Implementation notes.** Publish the generated OCEL 2.0 credit log as a standalone dataset with a DOI. There is currently no public financial-services agent log in OCEL 2.0 format, so this is a citable contribution in its own right and it strengthens the paper independently of the results.

**Acceptance tests.**
- `test_quickstart_commands_succeed` (run the README commands in a clean container)
- `test_published_log_validates` (Ocelint clean)

**DoD.** A fresh container reproduces the headline table.

---

## 10. Schedule and gates

| Week | Phases | Gate |
|------|--------|------|
| 1 | P0, P1, P2 | Qualified relations survive pm4py round trip; spans emitted |
| 2 | P3, P4, P5 (T1, T4, T5) | Recall 1.0 on the single split for the three simple templates |
| 3 | P5 (T2, T3), P6, P7 | Evidence chain valid; flattening-loses-violations result confirmed |
| 4 | P8, draft writing | `make all` reproduces results |
| Sep 1 to 4 | Abstract | Submitted |
| Sep 5 to 11 | Paper polish, P9 | Submitted |

**Gate rule.** If a week's gate is not met by its end, invoke the fallback in section 12 rather than compressing the next week.

---

## 11. Division of labour

**Aziz (technical, phases 0 to 3 and 5 to 9):** OCEL model, mapper, constraint engine, evidence renderer, baselines, evaluation.

**Alina (content, feeding phases 4, 6, 8):**
- `rules/crosswalk.yaml`: the article and standard mapping, Articles 12, 14, 26, 72 plus ISO/IEC 24970, ISO/IEC 42001, NIST AI RMF
- The natural-language constraint statements and their derivation from regulatory text, including a traceability matrix
- Review of `data/catalogue/violations.yaml` for realism
- Design and execution of the auditor inspectability study (E4)
- Related-work positioning *[A9 — amended, works identified]*: **Traccia**
  (arXiv:2607.14309 — closest competitor, must be cited and differentiated),
  SAP Agent Behavior Mining (Vu et al., BPM 2026, arXiv:2606.20669 + Signavio
  product), IBM process observability (arXiv:2505.20127), DEMM (Solozobov,
  arXiv:2605.04093 + DEMM-Bench arXiv:2606.20634). "TRAC" was unresolved —
  Alina confirms the intended reference (likely Traccia).
- Expert studies per `docs/EXPERT-STUDY-PROTOCOL.md` (Alina owns; formative
  Study B pilot committed for 1–8 Sep, descope auto-triggers 4 Sep if
  unscheduled)

Alina's deliverables are data files and prose, not code. The harness should never generate the crosswalk content itself; it builds the loader and the validator and leaves the file for her.

---

## 12. Risk register

| Risk | Trigger to watch | Fallback |
|------|------------------|----------|
| pm4py loses qualified relations | Phase 1 test fails | Write our own OCEL 2.0 serialiser for JSON only; drop XML and SQLite support |
| Live instrumentation unreliable | Phase 2 or 3 blocked past week 1 | Hand-author the OCEL log directly from a scripted scenario; drop claim C2 and E2, keep C1, C3, C4 |
| Scenario judged unrealistic by auditors | Phase 4 review | Reseed on BPI Challenge 2017 loan control-flow so the process shape is real even if agents are simulated |
| Detection results too close to the judge baseline | Phase 8 | Lead the paper on determinism and inspectability, which are unassailable, rather than on F1 |
| SAP publishes the object-centric extension first | Any time | Pivot the framing to the evidence layer, which they have shown no interest in, and cite them as concurrent work |
| T3 delegation graph too complex | Week 3 | Ship four templates instead of five; T3 is the least central to the AI Act mapping |

**The single biggest risk is not technical.** It is that the synthetic log is not credible to an FSI auditor, which would undermine the paper's central claim. Phase 4's human review is the mitigation and it must not be skipped.

---

## 13. CLAUDE.md starter

Put this at the repo root.

```markdown
# The Auditor's Trace

## What this is
A pipeline turning LLM-agent telemetry into EU AI Act audit evidence via
object-centric process mining. Research artefact for ICPM 2027.

## Hard rules
1. Determinism is the paper's headline claim. Never introduce nondeterminism
   into the analysis path: no unsorted iteration, no wall-clock in hashes,
   no LLM calls outside scenario/ and baselines/llm_judge.py.
2. Canonical JSON everywhere:
   json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
3. No new dependencies without asking. See BUILD-PLAN section 4.
4. Every constraint must carry a legal article reference. Rulesets without
   one fail validation by design; do not relax this.
5. data/catalogue/violations.yaml is frozen and tagged. Never edit it after
   the tag. If it is wrong, stop and report.

## Workflow
- Work one phase at a time, in order. See BUILD-PLAN section 9.
- Write the named acceptance test first, watch it fail, then implement.
- Run `make check` after every phase and report the results.
- If a phase cannot be done as specified, stop and explain. Do not substitute
  a different approach silently.

## Commands
make check     # lint, typecheck, test
make scenario  # generate spans
make ingest    # spans -> OCEL
make evaluate  # run all systems, compute metrics
make all       # everything, from seed data to figures
```

---

## 14. First command to give the harness

> Read `BUILD-PLAN.md` in full. Then execute Phase 0 only. Create the repository scaffold exactly as specified in section 3, with every module present as a stub containing only its docstring and the function signatures implied by later phases. Set up `pyproject.toml` with the exact dependency list from section 4, all pinned. Write the `Makefile` with the targets listed in section 13. Create `CLAUDE.md` with the content from section 13. Do not implement any logic yet. When `make check` passes on a clean checkout, stop and report.

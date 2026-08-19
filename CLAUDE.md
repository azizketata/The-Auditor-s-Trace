# The Auditor's Trace

## What this is

A pipeline turning LLM-agent telemetry into EU AI Act audit evidence via
object-centric process mining. Research artefact for ICPM 2027.

## Hard rules

1. Determinism is the paper's headline claim. Never introduce nondeterminism
   into the analysis path: no unsorted iteration, no wall-clock in hashes,
   no LLM calls outside `scenario/` and `baselines/llm_judge.py`.
2. Canonical JSON everywhere:
   `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
3. No new dependencies without asking. See BUILD-PLAN.md section 4.
4. Every constraint must carry a legal article reference. Rulesets without
   one fail validation by design; do not relax this.
5. `data/catalogue/violations.yaml` is frozen and tagged. Never edit it after
   the tag. If it is wrong, stop and report.

## Workflow

- Work one phase at a time, in order. See BUILD-PLAN.md section 9.
- BUILD-PLAN.md was amended 13 Aug 2026 after an evidence-based deep review;
  the amendments are marked *[A#/B#]* inline and their rationale lives in
  docs/PLAN-REVIEW.md. Phases 4-8 build to the AMENDED spec (span-level
  injection, held-out fault set + firewall, modernised judge, CIs/McNemar,
  cross-platform determinism). The expert studies follow
  docs/EXPERT-STUDY-PROTOCOL.md.
- Write the named acceptance test first, watch it fail, then implement.
- Run `make check` after every phase and report the results.
- If a phase cannot be done as specified, stop and explain. Do not substitute
  a different approach silently.

## Commands

```
make check     # lint, typecheck, test
make scenario  # generate spans
make ingest    # spans -> OCEL
make evaluate  # run all systems, compute metrics
make all       # everything, from seed data to figures
```

Targets for phases not yet built exit 1 with the phase they need. That is
deliberate — see hard rule 5 above and BUILD-PLAN.md section 0 rule 6.

## Current state

Phases 0, 1, 2, and 3 complete (Phase 2 was pulled ahead of Phase 1 by
explicit decision — it emits spans and never touches the OCEL model). Built:

- `model/span_contract.py` — the `at.*` governance span vocabulary
  (`at-span/1`), THE contract between the scenario (writer) and Phase 3's
  mapper (reader). Change it only with a version bump.
- `model/ocel_schema.py` — enums, the attribute-kind registry, and the
  qualifier matrix (bound 1:1 to the golden scenario spans by test).
  `Qualifier.DECLARES` is OCEL-level only, never in `at.*` spans.
- `model/log.py` — the immutable OCEL model, validation rules R1–R15, and
  `log_hash` (canonical JSON over the model, never file bytes). Stdlib-only.
- `model/io.py` — pm4py round trips in JSON/XML/SQLite, the only module
  that touches pandas. Values written as strings, decoded by the registry;
  JSON/XML post-passes make the files official-schema-valid (see
  docs/SPIKE-pm4py-roundtrip.md and tests/integration/test_ocel_validation.py).
- `tests/fixtures/mini_log.json` + `build_mini_log()` in tests/conftest.py —
  twin fixtures (file ↔ model bound by test) with a pinned golden
  `log_hash` the CI matrix asserts on Windows and Ubuntu.
- `scenario/` — the four-agent LangGraph fleet. 28 events/session, all 13
  event types, all 12 object types. `make scenario` runs it scripted.
- `ingest/` — the Phase 3 mapper: `otel_reader.py` (strict manifest-verified
  reader + span trees), `attribute_map.py` (canonical vocabulary folding
  both `gen_ai.*` and `llm.*`, C2 coverage report), `mapper.py` (two-pass
  per-trace mapping, cross-session object merge, `declares` synthesis, and
  the span-index sidecar §8 evidence records cite), `__main__.py` (exit
  codes 0/2/3/4/6/7). A pure function of span-file content (B1 — Phase 4
  re-maps injected spans through it). `make ingest` runs it;
  `results/e2_coverage.json` is committed by `.gitignore` exception.

- `scenario/injector.py` — the Phase 4 span-level violation injector:
  registry-based fault functions V1–V8 (extension seam for the firewalled
  held-out set), surgery through the span contract only, three splits
  (clean/single/mixed) with stratified quotas, D1 authority-boundary
  distractors, canonical manifests + `labels.json` ground-truth sidecars.
  `python -m auditors_trace.scenario inject` runs it; each split is
  ingestible with the unchanged ingest CLI.
  `data/catalogue/violations.yaml` exists as a PRE-REVIEW DRAFT — the
  freeze (tag `catalogue-v1` + push + external timestamp, invariant I4)
  is gated on the Study A realism review; the Phase 5 parameter set now
  exists (`rules/rules.yaml`).

- `constraints/` — the Phase 5 engine: `templates.py` (T1–T5, the shared
  `Violation` contract every detection system emits, `LogIndex`),
  `standard.py` (object-scoped DECLARE analogues; the attribute-predicated
  `object_absence` carries `STD.policy_version_current`), `ruleset.py`
  (pydantic schema; a rule without a legal article reference fails at load —
  I3), `engine.py` (registry pinned 1:1 to `KNOWN_TEMPLATES`, canonical
  violation order, `engine_version()`). `rules/rules.yaml` transcribes its
  params from `scenario_credit.yaml` (drift-guarded by
  tests/unit/test_rules_content.py) and is an I4 freeze-bundle item.
  Verified: recall AND precision 1.0 on the full single split (240/240 exact
  matches on `(constraint_id, event_ids)`), mixed fully matched, clean split
  silent, 100-run determinism. Per B2 these are verification gates, never
  reported results. `eval/metrics.py::confusion`/`precision_recall_f1` were
  pulled forward from Phase 8 (I4 freezes the matching function); the
  `matcher=` keyword is the seam the Phase 7 judge matcher plugs into.
  T1's violation anchors are case-dependent (V1: decision; V2:
  approval+decision; V3: approval only) and T3 guarantees one violation per
  (session, orphaned agent) by excluding orphan-cited handoffs from its
  cycle check — anchor changes here break the exact ground-truth join.

- `evidence/` — the Phase 6 renderer: `record.py` (§8 pydantic models, exact
  field inventory, substance-validated), `chain.py` (record_hash excludes
  integrity+generated_at structurally; chain in engine-canonical order;
  verify_chain RECOMPUTES hashes — the tamper test; `records_jsonl` is the
  single byte-level bundle authority), `crosswalk.py` (loader takes the
  required-id set; the all-TODO template can never load; cross-validated
  against the ruleset), `renderer.py` (RenderIndex prebuilt from log +
  span-index sidecar — the renderer never re-queries the log; typed errors),
  `pack.py` (stdlib markdown study pack). `cli.py` implements `check` (the
  §8 rerun command; exit codes 0/2/3/4/6/7; raw-bytes output) and `pack`
  (regenerates the committed docs/study/evidence-example.md).
  `rules/rules.yaml` gained a required `formal` field per rule (I4 bundle).
  `rules/crosswalk.yaml` is intentionally ABSENT (its content is
  human-authored, §11); the loadable stand-in is
  tests/fixtures/crosswalk_fixture.yaml, clearly labelled; evidence goldens
  (tests/golden/evidence/, cut via _regen.py) re-cut when the real crosswalk
  lands, pre-freeze. GT span agreement is subset-shaped (labels pre-register
  manifestation spans beyond the anchor events for V3/V6-B). Week-3 gate
  "evidence chain valid" met; B4's bundle check = the golden bitwise test on
  the CI matrix.

Beware: `data/generated/ocel/*.jsonocel` are convenience artefacts that go
stale when splits are regenerated — verification always re-maps from each
split's `manifest.json` (sha256-verified). Re-running the ingest CLI also
rewrites `results/e2_coverage.json`; that committed file belongs to the
50-session `make ingest` base run — restore it if a splits ingest clobbers it.
`docs/study/evidence-example.md` is likewise derived: regenerate via
`cli pack` (recompute-tested), never hand-edit.

Still stubs: `baselines/`, and in `eval/` everything except
`confusion`/`precision_recall_f1`.
`data/catalogue/scenario_credit.yaml` is the scenario catalogue — distinct
from `violations.yaml` (draft; frozen only at the I4 freeze commit).
Do NOT edit `scenario_credit.yaml`, even its comments: its byte hash feeds
every run id, so any edit invalidates all span goldens. (Its header still
says injectors "mutate the OCEL log" — outdated since B1; injection is
span-level. The correction lives here instead of in the file.)

Known spec deviations, both surfaced and approved: the Claude API has no
sampling seed (recorded as provenance instead), and `temperature` is only
accepted on Haiku 4.5 (hence the fleet model choice).

## Code navigation

1. `graphify query "<question>"` — knowledge graph, scoped subgraph, no API cost.
2. Serena MCP — LSP symbol lookup and symbol-level edits, instead of reading
   whole files to locate a definition.
3. Grep/Glob — only when those come up empty.

Use Context7 MCP before writing against pm4py, pydantic, LangGraph, or the
OpenTelemetry SDK. All four move fast; assume training data is stale.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

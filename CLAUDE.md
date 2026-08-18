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

Phases 0 and 2 complete (Phase 2 was pulled ahead of Phase 1 by explicit
decision — it emits spans and never touches the OCEL model). Built:

- `model/span_contract.py` — the `at.*` governance span vocabulary
  (`at-span/1`), THE contract between the scenario (writer) and Phase 3's
  mapper (reader). Change it only with a version bump.
- `model/ocel_schema.py` — the three enums are real; the two functions stay
  Phase 1 stubs.
- `scenario/` — the four-agent LangGraph fleet. 28 events/session, all 13
  event types, all 12 object types. `make scenario` runs it scripted.

Still stubs: `model/log.py`, `model/io.py` (Phase 1), `ingest/` (Phase 3),
`constraints/`, `evidence/`, `baselines/`, `eval/`, `scenario/injector.py`
(Phase 4). `data/catalogue/scenario_credit.yaml` is the scenario catalogue —
distinct from `violations.yaml`, which does not exist yet and gets frozen at
Phase 4.

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

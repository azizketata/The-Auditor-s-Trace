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

Phase 0 complete: scaffold only. Every module under `src/auditors_trace/` is a
stub whose functions raise `NotImplementedError`. Placeholder types (`OCELLog`,
`Violation`, `EvidenceRecord`, ...) are declared but empty; Phase 1 replaces the
model ones with BUILD-PLAN.md section 5's real definitions.

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

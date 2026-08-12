# The Auditor's Trace

<!-- Keep this file under ~200 directives. Every line is loaded into context on every
     session — delete anything that stops being true rather than letting it rot. -->

## Project

_Not yet described. Fill in: what this builds, who uses it, the one-sentence goal._

## Stack

_Not yet chosen. Record languages, frameworks, and package manager here once set._

## Commands

_Not yet defined. Record the build / test / lint / run commands here once they exist —
this is the highest-value section of the file._

## Working agreement

- Plan before non-trivial edits (shift+tab → plan mode). Get the plan approved, then build.
- For anything with more than one moving part, use the spec-kit flow rather than
  improvising: `/speckit-specify` → `/speckit-clarify` → `/speckit-plan` →
  `/speckit-tasks` → `/speckit-implement`. Specs live in `specs/`.
- Prefer editing existing files over creating new ones. Do not add README or docs files
  unless asked.
- Match surrounding code: its naming, comment density, and idiom. Do not add explanatory
  comments the rest of the file would not have.
- Never commit or push unless explicitly asked.
- Never commit secrets. `infra/langfuse/.env` and any `.env` are gitignored — keep it so.

## Code navigation

Search order for "where is X / how does Y work" — cheapest first:

1. `graphify query "<question>"` — knowledge graph, scoped subgraph, no API cost.
2. Serena MCP — LSP symbol lookup (`find_symbol`, `find_referencing_symbols`) and
   symbol-level edits. Use it instead of reading whole files to locate a definition.
3. Grep/Glob — only when the two above come up empty.

Read whole files only when you genuinely need the whole file.

## Library docs

Use Context7 MCP before writing against any third-party API. It returns docs pinned to
the version in the lockfile — assume your training data is stale for anything moving.

## Browser + testing

- Playwright MCP for driving the app and writing E2E tests.
- Chrome DevTools MCP for console errors, network waterfalls, and performance traces.
- Do not claim a UI change works until it has been exercised in a real browser.

## Observability

LLM calls are traced to self-hosted Langfuse at `http://localhost:3000`
(`infra/langfuse/`, `docker compose up -d`). Instrument with OpenTelemetry GenAI
semantic conventions — span names `chat <model>` / `execute_tool <name>`, attributes
under `gen_ai.*`. Do not hand-roll a tracing format.

## Review

- `/code-review` before opening a PR; `/security-review` when the diff touches auth,
  input parsing, file paths, or anything network-facing.
- Greptile reviews PRs on GitHub automatically; its comments are readable from here via
  the greptile MCP tools.

## Conventions

_Add project-specific rules here as they are decided — naming, error handling, test
layout, directory structure. Each one earns its place by having been gotten wrong once._

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

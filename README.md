# The Auditor's Trace

Deterministic, human-inspectable, article-level EU AI Act audit evidence from
LLM-agent telemetry, via object-centric process mining.

Research artefact for ICPM 2027. Aziz Ketata (technical), Alina Hafner
(regulatory, evaluation design).

## The problem

LLM agents in credit scoring are classified high-risk under Annex III point
5(b) of the EU AI Act, and the high-risk obligations apply from 2 December
2027 (Regulation (EU) 2026/1744, amending 2024/1689). Article 12 requires
logging sufficient to reconstruct a decision after the fact; Article 26(6)
requires deployers to retain those logs for at least six months; Article 72
requires post-market monitoring fed by them.

Observability tooling produces dashboards. LLM-as-judge evaluation produces
non-reproducible opinions. Neither is audit evidence.

## The pipeline

```
instrumented agent fleet
        │  OpenTelemetry / OpenInference spans
        ▼
   span → OCEL 2.0 mapper
        │  OCEL 2.0 log (objects + qualified relations)
        ▼
   object-centric constraint engine
        │  violation set
        ▼
   evidence renderer
        │  signed, hash-chained evidence records
        ▼
   retention store + evaluation harness
```

## Quickstart

Requires Python 3.11, [uv](https://docs.astral.sh/uv/), and GNU Make.

```bash
uv sync --all-extras
make check
```

On Windows: `winget install ezwinports.make`.

## Status

**Phase 2 of 9 — the agent fleet.** The scaffold (Phase 0) and the
credit-scoring scenario are built; Phase 1 (OCEL model + I/O) is next. See
[BUILD-PLAN.md](BUILD-PLAN.md) for the full phase plan and its gates.

```bash
make scenario                     # 50 sessions, scripted backend, no key needed
make scenario PROVIDER=anthropic  # the artefact run; needs ANTHROPIC_API_KEY
```

Spans land in `data/generated/spans/` — one JSONL file per session plus a
`manifest.json` the ingest phase iterates.

## A note on determinism

Running the analysis pipeline twice on the same input produces byte-identical
evidence records. That is the paper's headline claim, and it is enforced
continuously: canonical JSON, sorted iteration everywhere, no wall-clock value
inside any hashed payload, pinned dependencies, `PYTHONHASHSEED=0`.

**The simulated agent fleet is a deliberate exception.** LLM output is not
bitwise reproducible with a real provider, and it does not need to be.
Determinism is required of the *analysis* pipeline, not of the subject under
test. The fleet's spans are captured once and committed as fixed input;
everything downstream of them reproduces exactly.

Three related facts, recorded here so the paper states them plainly:

- **OCEL event time is simulated.** Windows' wall clock resolves to 15.625 ms
  and sibling spans tie constantly, so ordering by span time would be random.
  Every event carries `at.event.seq` (the ordering key) and `at.event.time`
  from a deterministic business clock; span nanoseconds remain real telemetry.
- **The Claude API has no sampling-seed parameter** on any current model, so
  BUILD-PLAN §9's "fixed seed on every LLM call" is recorded as provenance
  (`model_seed` on every `call_llm` event), not enforced as sampling.
  `temperature=0` *is* genuinely set — `claude-haiku-4-5` is the fleet model
  precisely because current Opus/Sonnet models reject the parameter.
- **The scripted backend** (`--provider scripted`, the default) drives the same
  LangChain/LangGraph path through the same OpenInference instrumentation, so
  span *structure* is genuine; only token content is scripted, and the model is
  honestly named `scripted-credit-policy-v1` — never a vendor model id.

LLMs appear in exactly two places in this repository: inside the simulated fleet
(`src/auditors_trace/scenario/`) and inside the LLM-as-judge baseline
(`src/auditors_trace/baselines/llm_judge.py`). Nowhere else.

## Licence

Apache-2.0. The Statlog German Credit dataset (UCI id 144) is CC BY 4.0 and is
downloaded at build time, never committed.

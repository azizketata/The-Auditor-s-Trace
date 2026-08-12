# The Auditor's Trace

Deterministic, human-inspectable, article-level EU AI Act audit evidence from
LLM-agent telemetry, via object-centric process mining.

Research artefact for ICPM 2027. Aziz Ketata (technical), Alina Hafner
(regulatory, evaluation design).

## The problem

From 2 December 2027, LLM agents in credit scoring are high-risk systems under
Annex III point 5(b) of the EU AI Act. Article 12 requires logging sufficient to
reconstruct a decision after the fact; Article 26(6) requires deployers to retain
those logs for six months; Article 72 requires post-market monitoring fed by them.

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

**Phase 0 of 9 — scaffold.** Every module is a stub; `make check` passes. See
[BUILD-PLAN.md](BUILD-PLAN.md) for the full phase plan and its gates.

## A note on determinism

Running the analysis pipeline twice on the same input produces byte-identical
evidence records. That is the paper's headline claim, and it is enforced
continuously: canonical JSON, sorted iteration everywhere, no wall-clock value
inside any hashed payload, pinned dependencies, `PYTHONHASHSEED=0`.

**The simulated agent fleet is a deliberate exception.** LLM output is not
bitwise reproducible even at temperature 0 with a fixed seed, and it does not
need to be. Determinism is required of the *analysis* pipeline, not of the
subject under test. The fleet's spans are captured once and committed as fixed
input; everything downstream of them reproduces exactly.

LLMs appear in exactly two places in this repository: inside the simulated fleet
(`src/auditors_trace/scenario/`) and inside the LLM-as-judge baseline
(`src/auditors_trace/baselines/llm_judge.py`). Nowhere else.

## Licence

Apache-2.0. The Statlog German Credit dataset (UCI id 144) is CC BY 4.0 and is
downloaded at build time, never committed.

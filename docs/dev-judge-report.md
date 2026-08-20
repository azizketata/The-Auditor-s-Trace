# Judge prompt development report (dev split only)

> **B2 discipline — read first.** Every number in this file is
> prompt-development telemetry measured on the DEV split (seed-4207 partition
> of the 50-session base run `e1f8535c07681d95`, disjoint from the evaluation
> base run `f95aed68e789bb84`). Nothing here is an evaluation result and
> nothing here may be reported as one. Evaluation runs happen only after the
> `catalogue-v1` freeze, on the evaluation splits, through the
> `--allow-evaluation` gate.

## Protocol

Per iteration: `python -m auditors_trace.cli judge --model claude-haiku-4-5
--model claude-sonnet-5 --samples 1 --condition both` (defaults target
`data/generated/dev/single`, 32 labelled sessions), then parse rate,
`judge_match` precision/recall, and `session_match` rate against
`data/generated/dev/single/labels.json` are recorded below. Stop criteria:
parse rate ≥ 95% on both models × both conditions AND recall improvement
< 2 points vs the previous iteration; hard stop at 4 iterations or $15
cumulative. Opus is excluded from development (cost, and the prompt must not
overfit the strongest model) except one final smoke session. After the final
iteration the observed judge labels extend `rules/judge_matching.yaml`'s
synonym map and `prompt_version` moves to `1.0.0` — the freeze candidate.

## Status

**BLOCKED on `ANTHROPIC_API_KEY`** (not set in the build environment when
Phase 7 landed). To run iteration 1:

```powershell
$env:ANTHROPIC_API_KEY = "<key>"
uv run python -m auditors_trace.cli judge --model claude-haiku-4-5 --model claude-sonnet-5 --samples 1 --condition both
```

The harness, cache, lock, and prompts (`prompt_version: dev-1`) are fully
built and hermetically tested; the scripted provider covers CI.

## Iterations

| # | date | prompt_version | model | condition | parse rate | judge_match P / R | session rate | est. cost |
|---|------|----------------|-------|-----------|-----------|-------------------|--------------|-----------|
| — | —    | dev-1          | —     | —         | pending   | pending           | pending      | $0        |

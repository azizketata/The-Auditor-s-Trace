# Golden mapper outputs

Byte-exact outputs of the Phase 3 mapper for the frozen span fixtures in
`tests/golden/spans/`. `test_mapper_golden_small` compares against these
byte for byte; the pinned `log_hash` constants live in
`tests/integration/test_mapper_golden.py` and are asserted identically on
Windows and Ubuntu by CI.

| Trio (`.ocel.json` / `.coverage.json` / `.span_index.json`) | Source fixture |
|---|---|
| `credit_grant_approval_seed42.*` | `spans/credit_grant_approval_seed42.jsonl` |
| `credit_deny_approval_refer_seed2.*` | `spans/credit_deny_approval_refer_seed2.jsonl` |
| `paired_vocabulary.*` | `spans/paired_vocabulary_genai.jsonl` — and `paired_vocabulary_openinference.jsonl` must reproduce the **same** trio (`test_both_vocabularies_map_to_same_ocel`) |

Regeneration (only after a deliberate, reviewed mapper change):

```
uv run python -m auditors_trace.ingest run \
    --spans tests/golden/spans/<fixture>.jsonl \
    --out tests/golden/mapper/<name>.ocel.json \
    --coverage tests/golden/mapper/<name>.coverage.json \
    --span-index tests/golden/mapper/<name>.span_index.json
```

Then update the pinned hashes in the test file and review the diff. The
inputs are frozen (see `spans/README.md`); if these outputs move without a
mapper change, determinism broke — stop and find out why.

# Spike report — pm4py OCEL 2.0 round trip (Phase 1 gate)

*19 Aug 2026. BUILD-PLAN Phase 1 DoD: "If qualified relations do not survive
a pm4py round trip, stop and report before proceeding." PLAN-REVIEW.md B18
made this the opening step of Phase 1. Pinned as executable facts in
`tests/integration/test_pm4py_roundtrip.py`; pm4py 2.7.23.4.*

## Verdict

**Gate PASSED — qualified E2O and O2O relations survive all three OCEL 2.0
serializations (JSON, XML, SQLite).** No §12 fallback needed. Three platform
behaviors constrain the Phase 1 model, all three absorbable by construction.

## Findings

**F1 — pm4py exporters delete relation-less objects, and O2O edges with
them.** Every OCEL 2.0 exporter unconditionally runs
`filtering_utils.propagate_relations_filtering` before writing
(`pm4py/objects/ocel/exporter/jsonocel/variants/ocel20.py` line 66; same in
xml/sqlite variants). It intersects the object table with the objects that
appear in E2O relations, then drops every O2O row with a missing endpoint
(`pm4py/objects/ocel/util/filtering_utils.py` lines 241–256). An object with
zero E2O relations never reaches the file. This kills the naive "pure
declaration" mapping for the superseded PolicyVersion (V8 must repoint to it,
and its `derived_from` O2O edge must survive) and the unretrieved
DataResources (V4 reasons over their absence).

**F2 — the fix is already implied by the span contract.** The declaration
invariant guarantees every object is declared by exactly one event per trace.
Materializing each *pure* declaration (a qualifier-less object entry in
`at.*`) as a qualified E2O relation `declares` from the declaring event makes
every object reachable; with that, XML and SQLite round-trip **everything**
losslessly: events, objects, both attribute tables, E2O qualifiers, O2O
qualifiers, sub-second timestamps. Verified empirically, pinned in
`TestP2DeclaresMaterializationRoundTrips`.

**F3 — pm4py's JSON importer deduplicates E2O by (event, object) pair,
keeping the last.** Two qualifiers between the same event and object are
written to the file correctly but only one survives a read. JSON exporter
also truncates timestamps to whole seconds (`clean_dataframes.py`,
`strftime("%Y-%m-%dT%H:%M:%SZ")`). XML and SQLite have neither defect.

## Design constraints adopted (Phase 1 model layer)

| # | Rule | Absorbed by |
|---|---|---|
| G1 | Every OCEL object carries ≥ 1 qualified E2O relation; pure declarations materialize as `declares` from the declaring event | New `Qualifier.DECLARES`, OCEL-level only — never emitted in `at.*` spans (span contract `at-span/1` unchanged; `E2O_QUALIFIERS` in `span_contract.py` deliberately excludes it) |
| G2 | At most one E2O relation per (event, object) pair — `OCELLog` validates this | Scenario already satisfies it; validation turns habit into guarantee (F3) |
| G3 | OCEL timestamps are whole-second UTC | `scenario/clock.py` already emits `%Y-%m-%dT%H:%M:%SZ` exactly (F3) |
| G4 | The canonical log hash is computed over canonical JSON of the in-memory model, never over serialized file bytes | Already BUILD-PLAN §8's rule; F1–F3 confirm file bytes are not a stable identity |

Under G1–G3 all six Phase 1 acceptance tests are satisfiable as named,
including all three `test_roundtrip_*_preserves_log` variants — "preserves"
means model → write → read → model equality, which G1–G3 make exact.

## Spec delta

`Qualifier` grows a 16th member, `DECLARES` (12 span-level E2O + 3 O2O +
1 OCEL-level). `test_all_object_and_event_types_appear` now asserts span
qualifiers equal `set(Qualifier) - {DECLARES}`. Decision taken under the
standing delegation (A. Ketata, 16 Aug); surfaced at the Phase 1 gate.

# BUILD-PLAN deep review — verified facts and amendment register

*13 Aug 2026. Produced by a five-agent verification + hostile-critique pass
(legal facts, related work, venue/methodology, research-design critique,
evaluation critique), synthesised by the build harness. Status of every fact
was independently web-verified; citations inline. This document is the
amendment register for Phases 4–8 — BUILD-PLAN.md itself is amended only by
author decision.*

## Verdict in one paragraph

The plan is **fundamentally sound and worth executing**: the engineering
invariants are mechanically enforced rather than aspirational, the gap is
verified as real-but-narrow, the deadlines are the actual ICPM 2027 deadlines,
and the pre-registration instinct is the right one. But as written, **three of
the four headline claims rest on things the authors control rather than
test**, one legal framing is refuted, and one architectural decision (where
violations are injected) silently invalidates the baseline comparison. All of
it is fixable inside the existing four-week phase plan; none of it requires
new machinery. Fix the amendments below and this is a strong, honest ICPM
submission whose determinism and evidence-layer contributions hold even if the
detection deltas narrow.

## A. Verified facts (what the paper may safely claim)

| # | Fact | Status | Consequence |
|---|---|---|---|
| A1 | **2 Dec 2027** is correct: Digital Omnibus adopted as **Regulation (EU) 2026/1744** (OJ 24 Jul 2026, in force 27 Jul 2026), postponing Annex III high-risk *obligations* to a fixed 2 Dec 2027 | Confirmed | Cite 2026/1744. Phrase as "obligations **apply from** 2 Dec 2027" — the high-risk *classification* is unchanged. Paper lands ~3 months before the compliance date for exactly this system class. |
| A2 | Annex III 5(b) covers creditworthiness evaluation of natural persons; sole carve-out is financial **fraud detection** | Confirmed | Scenario must stay creditworthiness, never fraud detection. |
| A3 | Art. 12(1)–(2) ground the logging constraints; **Art. 12(3)'s minimum log content is biometric-only** | Confirmed | Never derive log-content requirements from 12(3). Use ISO/IEC FDIS 24970 for content expectations. |
| A4 | Art. 26(6): ≥ 6 months retention + financial-institutions documentation clause | Confirmed | Retention block in §8 is safe as specified. |
| A5 | **Art. 10(5) as basis for sex/age proxies is REFUTED**: 10(5) lifts the GDPR Art. 9 prohibition, and sex/age are not Art. 9 categories — there is no prohibition to lift | **Refuted** | Re-anchor bias-examination constraints on **Art. 10(2)(f)–(g)**. Foreign-worker status is the only plausibly Art. 9-adjacent attribute (data *indirectly revealing* ethnic origin, CJEU C-184/20 broad reading — Alina to confirm). 10(5)(f)-style documentation becomes a *voluntary conservative safeguard applied by analogy*, never the legal basis. Fixed in the repo 13 Aug (catalogue, crosswalk template, brief, code comments). |
| A6 | Art. 14(4)(d)–(e) fit the approval/override constraint exactly; Art. 86(1) supports reason codes as *deployer-dischargeable* explanation duty (on-request; 86(3) subsidiarity) | Confirmed | Frame reason codes as evidence the deployer *can* discharge Art. 86, not a literal mandate. |
| A7 | **ISO/IEC 24970 is at FDIS** (stage 60.00, under publication), not a published IS | Uncertain→cite carefully | Cite "ISO/IEC FDIS 24970"; re-verify before 4 Sep and upgrade if published. ISO/IEC 42001:2023 is safe. |
| A8 | **ICPM 2027: 8–12 Feb 2027, Univ. of Calabria, Rende, Italy.** Abstract 4 Sep / paper 11 Sep 2026 are the official research-track deadlines. **ACM single-column "manuscript" format, max 13 pages incl. references** (not IEEE 8-page). ≥3 reviewers + discussion; blinding policy unconfirmed. CFP explicitly welcomes interview protocols/questionnaires as artifacts and requires generative-AI disclosure | Confirmed | Plan deadlines are real. Write for ACM 13-page. Publish protocol + consent + logs on Zenodo (CC-BY); consider 4TU.ResearchData for logs. Disclose AI-assisted development. Fallbacks at same venue: Demos (2 Nov), workshops (18 Nov — after 4 Nov notification). |
| A9 | The gap **survives but is narrow**: no existing work combines OCEL 2.0 + object-synchronised constraints + article-level AI Act crosswalk + determinism guarantee + judge baseline. Closest: **Traccia** (arXiv:2607.14309, Jul 2026 — OTel→AI-Act evidence packages, Articles 12/14/19/26(6)/50, hash fingerprints, *no process mining, no OCEL, no determinism claim*); SAP **Agent Behavior Mining** (arXiv:2606.20669, BPM 2026 + shipping product — PM on agent telemetry, *no legal mapping*); **OC-DECLARE** (Küsters & van der Aalst, BPM 2025, DOI 10.1007/978-3-032-02867-9_11) + their OCPQ tool (arXiv:2506.11541); **DEMM** (Solozobov, arXiv:2605.04093 + DEMM-Bench 2606.20634 — "container fallacy" framing device); IBM process observability (arXiv:2505.20127). "TRAC" in §11 is **unresolved** — likely Traccia; confirm with Alina | Confirmed | The claim must be the *conjunction*, stated precisely. Cite and differentiate Traccia explicitly. Space is crowding monthly — treat Sep deadlines as hard, no scope creep. |
| A10 | LLM-judge non-determinism is literature-backed: temp-0 judges still flip verdicts (arXiv:2606.26185, multi-provider); but **bit-deterministic inference now exists** (batch-invariant kernels — Thinking Machines, SGLang) | Confirmed | The judge comparison becomes a literature-predicted hypothesis, *and* the paper must preempt the batch-invariance counter: even a bit-deterministic judge is not third-party-reproducible evidence (mutable hosted service), unlike a rerun command over pinned artifacts. |
| A11 | pm4py: OCEL 2.0 support claimed complete by ocel-standard.org; **no known qualifier round-trip bug either way** | Uncertain | Don't cite a known issue. Phase 1's own round-trip test is first-party evidence; check pm4py-core issues at implementation time. |
| A12 | Expert-evaluation methodology: DSR expert opinion valid for artifact utility (Hevner 2004; Peffers 2007/2012; Wieringa); n=3–6 defensible as *formative* validation (Werner et al. two-auditor audit precedent; Guest et al. 2006); think-aloud with domain experts established (PLOS ONE 10.1371/journal.pone.0291443); Germany: no blanket Ethikkommission requirement for professional-opinion interviews, but documented ethics self-assessment + GDPR Art. 6(1)(a) consent expected | Confirmed | See docs/EXPERT-STUDY-PROTOCOL.md, which operationalises all of this. |

## B. Amendment register (binding for Phases 4–8 unless the authors override)

Severity: **F** = would sink the submission; **M** = weakens a claim; **m** = polish.

| # | Sev | Finding | Amendment |
|---|---|---|---|
| B1 | **F** | **Injection-point contradiction**: §9-P4 injects into the *OCEL log* while §9-P7 gives the judge *raw spans* — the spans never contain the faults, so the judge is structurally blind and evidence records cite span ids that don't exhibit the violation (breaks E3 *and* C4 traceability) | **Inject at span level** (pure function over the span JSONL, seeded), then re-map through the Phase 3 mapper so every system consumes artifacts derived from the same faulted spans. Bonus: exercises the mapper under fault conditions (strengthens C2). Phase 4's design changes before it is built. |
| B2 | **F** | **E3 circularity**: catalogue pre-declares "Detected by: T1…", same author writes injectors and templates, P5 DoD mandates recall 1.0 | Recall 1.0 on `single` is *verification* (a test), never a reported result. Evidential weight moves to: (i) a **held-out fault set** authored by Alina + the recruited auditor *after* templates freeze (author firewalled), sourced partly from the realism review's "what's missing?" answers; (ii) surface-form **perturbations** per injection; (iii) **near-miss compliant distractors** (approval at authority boundary, refer without approval, special-category read *with* basis); (iv) a **pre-registered judge-matching function** (constraint-class + event-overlap threshold) in the same tagged commit as the catalogue, plus blind adjudication of judge-flagged non-catalogue violations. |
| B3 | **F** | **C4's human half has no path into the submission** ("execution can slide" vs paper due 11 Sep); "signed" is unimplemented in §8's schema (hash-chained ≠ signed) | Decide claim set **before the 4 Sep abstract**: run a 2–3-participant formative walkthrough in the 1–8 Sep window (protocol ready — see EXPERT-STUDY-PROTOCOL.md) *or* descope C4 to reproducible + tamper-evident + published protocol-as-artifact. Replace "signed" with "hash-chained, tamper-evident" or implement an actual signature in Phase 6. |
| B4 | M | **D metric overclaims** (20/20 runs → CP lower bound ≈ 0.83; N inconsistent with 3×5; single-machine only; strawman framing) | Three-layer determinism claim: (1) by construction (tests); (2) **cross-platform bitwise hash equality** of the full evidence bundle (Windows+Linux CI matrix — cheap, already half-built); (3) judge variance reported with exact binomial CIs, distinct-verdict-set counts, per-item flip rates. Frame determinism as a *requirement whose cost is measured* (what detection performance, if any, is sacrificed), not a race the engine wins by definition. Note flat-DECLARE and OC-DFG also have D=1.0 — the differentiator is determinism *and* expressiveness jointly. |
| B5 | M | **Judge baseline not implementable as spec'd in Aug 2026** (no Anthropic seed; temperature 400s on frontier models; no structured outputs; information asymmetry; single-sample judging outdated) | Rewrite P7: pin 3 current frontier models with exact versions + access dates; "5 repeated samples" not "5 seeds" (measured quantity = end-to-end service variability, the auditor-relevant quantity); native structured outputs (schema-failure rates reported separately); judge gets rules *with article references* and both input conditions (spans / serialized OCEL) reported separately; prompt developed on a disjoint dev split, frozen in the tagged commit; add **majority-vote-of-5** as the strong judge variant; report per-session cost/latency; acknowledge agentic judges as future work. |
| B6 | M | **No inferential statistics; 50 sessions underpowered** (~6/class) | Stratify injections to ≥30 instances/class (~300+ sessions — cheap for deterministic systems; judge on stratified subsample if cost-bound). Wilson/CP CIs on rates, bootstrap CIs on F1, **McNemar's** for paired system comparisons. Add `test_metrics_report_confidence_intervals` to P8 acceptance tests. |
| B7 | M | **E1 is a claim, not an evaluation**; single Application flattening is self-fulfilling (handoffs lack an Application relation *by our own design*) | Two-part E1: analytical argument per constraint class (convergence/divergence pathology, scoped to single-case-notion XES + case-centric DECLARE, richer encodings conceded in threats); empirical **per-flattening × per-violation-class detectability matrix** across *every* viable case notion (Application, Session, CreditDecision, Agent) with duplication/loss counts. Defensible claim: *no single case notion covers all classes; cross-object synchronisation (V2) is inexpressible without object identity in any of them*. |
| B8 | M | **C2 self-graded** (writer, reader, and coverage denominator all authored together) | Split C2: (a) *design claim* — at.* as a proposed minimal governance instrumentation profile, necessity shown by a **layer-B-only ablation** (map gen_ai.*/openinference.* alone; report how little Art. 12-relevant content survives); (b) *fidelity claim* — coverage denominator derived externally from Art. 12(2)(a)–(c) + FDIS 24970 content expectations, authored by Alina, not by span_contract.py. |
| B9 | M | **Git-tag pre-registration is theater** (author-controlled, mutable, no temporal proof) | External timestamp before any detection run: push tag to public remote **and** third-party timestamp (Zenodo DOI deposit / OSF registration / RFC 3161). Freeze catalogue + rules.yaml + template parameters + verbatim judge prompts + matching function in the *same* tagged commit. Document role separation (who authored what, who was blind to what). |
| B10 | M | **Realism review risks being anecdote** | Run as structured expert content-validity review per EXPERT-STUDY-PROTOCOL.md Study A: per-V ratings (plausibility/frequency/severity) + free text + "what's missing?" elicitation (feeds B2's held-out set). Documented protocol in `data/catalogue/REVIEW.md`, written consent incl. quotation, instrument published as artifact. |
| B11 | m | Mixed-split composition semantics undefined (V1 deletes what V2 retargets) | Compatibility matrix of co-injectable faults or sequential injection with ground-truth recomputation; `test_composed_injections_have_consistent_ground_truth`. |
| B12 | m | **§8 schema self-reference bug**: `reproducibility.expected_record_sha256` sits inside its own hashed payload — fixed-point impossibility | Move it into the `integrity` block (excluded from hashing) or drop it. Catch before Phase 6 goldens are cut. |
| B13 | m | Ethics/GDPR paperwork unscheduled but on the critical path (~21 Aug call) | Consent form + info sheet before first contact (in EXPERT-STUDY-PROTOCOL.md); audio deleted after transcription; ethics self-assessment documented; Alina checks Apr 2026 EDPB research guidelines + university policy. |
| B14 | m | Scalability generation method unstated (fleet won't rerun at 5000) | State replication method explicitly; test that replicated logs preserve event-type distributions; label the plot *engine-runtime scaling*. |
| B15 | m | Realism surface: DM currency, fixed 28-event skeleton → near-zero variant count | Report control-flow variant statistics; ensure the ~8% decline branch + retrieval variations actually produce variants; currency-neutral field names where visible; keep BPI 2017 reseeding fallback pre-argued. |
| B16 | m | OCPQ unaddressed; "Ocelint" existence unverified; "TRAC" unresolved | Extend §4 justification to OCPQ or use it as a verdict-agreement check; replace Ocelint test with OCEL 2.0 XSD validation unless verified; resolve TRAC (likely Traccia) with Alina. |
| B17 | m | Paper won't fit as four claims + five experiment classes | Spine: **C3-as-cost-of-determinism + C4-reproducibility headline; C1 as motivating deficiency matrix; C2 as instrumentation-profile ablation**; pre-assign artifact/appendix material. ACM 13 pages makes this feasible. |
| B18 | M | Week-1 gate (pm4py round trip) still unretired while later phases stack on it | Run the pm4py qualifier round-trip **spike now** (1 day); it decides Phase 3's shape. Pre-commit the descope ladder: drop T3 → drop OC-DFG → shrink scalability sweep → workshop paper (18 Nov). |

## C. What was already fixed in the repo (13 Aug)

- Art. 10(5) → Art. 10(2)(f)–(g) re-anchoring in `data/catalogue/scenario_credit.yaml`,
  `rules/crosswalk.template.yaml`, `docs/BRIEF-alina.md`, and code comments;
  golden fixtures regenerated (A5).
- README phrasing: "obligations apply from 2 Dec 2027" + Reg. 2026/1744 (A1).
- ISO citation form "ISO/IEC FDIS 24970" in the crosswalk template (A7).

## D. Introduction ammunition (verified, citable)

- Art. 12 logging obligations for *existing* GPAI/transparency rules aside, the
  Annex III clock now runs to a fixed 2 Dec 2027 (Reg. 2026/1744) — the paper
  lands ~3 months ahead of the compliance date for its exact system class.
- Practitioner demand is visible in the wild: LangChain issue #35357 requests
  Article 12 audit logging.
- DEMM's "container fallacy" (collecting traces ≠ audit sufficiency) is a
  ready-made foil: this pipeline produces *checkable, re-derivable verdicts*,
  not evidence containers.
- Judge non-reproducibility at temperature 0 is now published fact
  (arXiv:2606.26185), turning our baseline comparison into a
  literature-predicted hypothesis.

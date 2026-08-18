# The Auditor's Trace — brief for Alina

*Status as of 13 Aug 2026. From Aziz (drafted with the build harness).*

## Where the build stands

The repository scaffold (Phase 0) and the simulated credit-scoring agent fleet
(Phase 2) are built and fully tested: four LangGraph agents (intake → data
retrieval → scoring → adjudication) plus a simulated human approval step,
emitting real OpenTelemetry traces. Each session produces 28 governance events
— approvals, data retrievals with lawful-basis records, policy-version
references, reason-coded decisions — over applicants drawn from the Statlog
German Credit dataset. Phases 1 and 3–9 (OCEL model, mapper, injector, engine,
evidence, baselines, evaluation, packaging) follow over the next ~3 weeks.

Deadlines: **abstract 4 Sep, paper 11 Sep** (ICPM 2027 cycle).

Your deliverables are data files and prose — no code, no tooling setup. Work
in whatever editor you like; the two YAML files can go back and forth by mail.

## 1. Start now: recruit the FSI auditor (longest lead time)

BUILD-PLAN §12 names our single biggest risk: *the synthetic log not being
credible to a financial-services auditor*. Two things need such a person:

- a ~30-minute realism review of the violation catalogue (next week), and
- the E4 inspectability study (participants inspect evidence records).

Recruiting has calendar lead time nothing else can compress — please start
asking around immediately, even before anything below.

## 2. Crosswalk (`rules/crosswalk.yaml`) — draft by ~25 Aug

The article/standard mapping that every evidence record embeds. A template
with the exact schema is at **`rules/crosswalk.template.yaml`** — copy it,
replace every TODO. The article numbers already in it are transcribed from
BUILD-PLAN §7 as starting points: please verify them, add paragraph-level
citations, and quote the actual requirement each imposes. Standards to map:
ISO/IEC 24970 (AI system logging), ISO/IEC 42001, NIST AI RMF. This feeds
Phase 6 (evidence renderer), which starts ~week of 25 Aug.

## 3. Natural-language constraints + traceability matrix — with the crosswalk

For the paper: each of T1–T5 needs a polished natural-language statement and a
derivation showing *how* it follows from the regulatory text (article →
obligation → observable log property). The template's `natural_language`
fields carry my rough versions to rewrite. The traceability matrix is prose/a
table for the paper, not code.

## 4. Legal framings already baked into the build — please vet these four

I had to make legal calls to keep building. Each is reversible, but each ends
up in the paper, so they need your eyes:

1. **Bias-examination framing (UPDATED 13 Aug after legal verification —
   see docs/PLAN-REVIEW.md, fact A5).** Our first framing cited Art. 10(5) for
   the sex/age/foreign-worker attributes; verified review refuted that: 10(5)
   only lifts the GDPR **Art. 9** prohibition, and sex/age are not Art. 9
   categories — no prohibition to lift. The repo now anchors the constraint on
   **Art. 10(2)(f)–(g)** (bias examination and mitigation), keeps the
   `special_category` label as a *deliberately conservative internal
   classification*, and applies 10(5)(f)-style record-keeping *by analogy as a
   voluntary safeguard*. **Your two questions:** (a) does this corrected
   framing hold? (b) is foreign-worker status data "indirectly revealing"
   ethnic origin under the CJEU's broad reading (C-184/20) — in which case
   Art. 10(5) genuinely applies to that one attribute? Exact text in
   `data/catalogue/scenario_credit.yaml` under `RES-DEMOGRAPHICS`.
2. **The lawful_basis wording** on that resource (and the GDPR 6(1)(b) lines
   on the ordinary resources) — please correct the citations/wording.
3. **Reason codes** (full list in the appendix below): deliberately none
   references a protected attribute; `RC98` records a human-override referral.
   Are these plausible as Art. 13/86 "principal reasons" language?
4. **Approval scope:** the decision outcomes requiring prior human approval
   are `{grant, deny}` — `refer` is the escalation itself and requires none.
   Sanity-check that reading of Art. 14/26(2).

## 5. Violation catalogue review — ~21 Aug, short turnaround

Phase 4 (next week) drafts `data/catalogue/violations.yaml`: eight injected
fault types (V1 missing approval, V2 approval referencing the wrong decision,
V3 unauthorised approver role, V4 missing mandatory data source, V5
special-category read without lawful basis, V6 adverse decision without reason
codes, V7 agent acting without handoff, V8 decision under a superseded
policy). You review it for realism — "would an auditor recognise these as
real failure modes?" — ideally together with the recruited auditor. The file
is then frozen and git-tagged **before** any detection experiment runs
(invariant I4), and your review is recorded in `data/catalogue/REVIEW.md`.
This is the cheapest insurance against our biggest risk; §12 says it must not
be skipped.

## 6. E4 inspectability study — design by ~28 Aug

Claim C4's human half: can an auditor reproduce and defend a violation from
its evidence record alone? You design the protocol (participants, task,
measures — e.g. can they independently re-derive the violation, rate
defensibility). Execution can slide past the abstract; the design should not.

## 7. Related work — by ~1 Sep

The deep review (docs/PLAN-REVIEW.md, fact A9) identified the exact works:

- **Traccia** (arXiv:2607.14309, Jul 2026) — the closest competitor:
  OTel-based AI-Act evidence packages mapped to Arts. 12/14/19/26(6)/50, with
  hash fingerprints — but no process mining, no OCEL, no determinism claim.
  Read the full paper before writing this section.
- **SAP Agent Behavior Mining** — Vu et al., BPM 2026 (arXiv:2606.20669) plus
  the shipping Signavio product: PM on agent telemetry, no legal mapping.
- **IBM** — Fournier et al. (arXiv:2505.20127), debugging-oriented process
  observability.
- **DEMM** — Solozobov (arXiv:2605.04093; DEMM-Bench arXiv:2606.20634); its
  "container fallacy" is a useful foil for our checkable-verdicts framing.
- **"TRAC" is unresolved** — the plan named it without citation and no such
  system was found. Please confirm what was meant (most likely Traccia;
  alternatives: TRACES arXiv:2605.27690, or the agent-provenance survey
  arXiv:2606.04990).

One paragraph each: what they do, what they don't (object-centric process
mining, cross-agent synchronisation constraints, bit-level deterministic
re-derivation, quantitative evaluation — the conjunction we occupy).

## Suggested timeline

| When | What |
|---|---|
| now | Start auditor recruiting (§1); skim this brief's §4 framings |
| ~21 Aug | Catalogue realism review (short call + written notes) |
| ~25 Aug | crosswalk.yaml draft + refined T1–T5 statements |
| ~28 Aug | E4 study design |
| ~1 Sep | Related-work positioning |
| 4 / 11 Sep | Abstract / paper |

## Appendix: the reason-code vocabulary (for §4.3)

Every adverse decision carries one or more of these; each evidence record
will cite them verbatim as the Art. 13/86 "principal reasons".

| Code | Statement |
|---|---|
| RC01 | checking account absent or persistently negative |
| RC02 | credit history shows past payment delays or a critical account |
| RC03 | savings insufficient relative to requested amount |
| RC04 | employment tenure below policy minimum |
| RC05 | installment burden high relative to disposable income |
| RC06 | requested amount large for the product class |
| RC07 | requested term long for the product class |
| RC08 | no realisable property or collateral |
| RC09 | existing credit obligations elsewhere |
| RC10 | reliance on co-applicant without guarantor standing |
| RC98 | human approver declined to endorse the automated outcome; referred for manual review |
| RC99 | aggregate score below approval threshold |

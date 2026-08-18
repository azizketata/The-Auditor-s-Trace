# Expert evaluation protocol — practitioner studies for The Auditor's Trace

*Version 0.1, 13 Aug 2026. Drafted by Aziz Ketata with the build harness, for
Alina Hafner to review, refine, and own (study design is her deliverable per
BUILD-PLAN §11). This protocol will be published as a research artifact
alongside the paper — the ICPM 2027 call explicitly lists interview protocols
and questionnaires among expected shared artifacts.*

---

## 1. Framing and rationale

Both studies are **design-science artifact evaluations by expert opinion**
(Hevner et al. 2004, MISQ; Peffers et al. 2007, JMIS; Wieringa 2014). Expert
opinion is accepted evidence for an artifact's *utility, realism, and
comprehensibility*; it is deliberately **not** used here to validate
algorithms — the deterministic pipeline carries all algorithmic claims through
its quantitative evaluation. Precedent for auditors evaluating process-mining
output as audit evidence: Werner et al., *Embedding process mining into
financial statement audits* (two-auditor evaluation); Jans et al. 2014.

Two studies, one participant pool, **two-touch design**: each expert is asked
for two short sessions rather than one long one — a realism-review call
(Study A, ~30 min, from ~21 Aug) and a walkthrough session (Study B,
~45–60 min, 1–8 Sep, once Phase 6 renders evidence records). Experts who can
only attend once are assigned to whichever study their availability matches;
a combined 75-min session is the fallback for late joiners after Phase 6.

- **Study A — Catalogue realism review** (content-validity review of the
  violation catalogue, before it is frozen). Feeds BUILD-PLAN Phase 4 and the
  E3 hardening (held-out fault elicitation).
- **Study B — Evidence inspectability walkthrough** (task-based think-aloud
  over generated evidence records; the E4 study, formative pilot first).

**Decision (13 Aug 2026, A. Ketata):** the formative Study B pilot is
**committed** for the 1–8 Sep window and reported in the submission; the
descope path in PLAN-REVIEW.md B3 remains as the documented fallback that
triggers automatically if no session is scheduled by the 4 Sep abstract
deadline. Rationale: practitioner evaluation is the one component no
competing work has (PLAN-REVIEW.md A9) and the held-out fault elicitation is
the load-bearing fix for the E3 circularity (B2) — expert access is treated
as a first-class contribution of the paper, not a validation checkbox.

## 2. Participants and recruitment

**Purposive sampling.** Inclusion criteria (documented per participant, in
anonymised form): current or recent role in **internal audit, model risk,
compliance, or supervision at (or serving) a bank or insurer in the EU**;
≥ 3 years professional experience; working familiarity with IT-supported or
model-related audits. Exclusion: co-authors' close collaborators.

**Target n:** 5–8 across both studies; **n = 2–3 is acceptable for the
formative pilot** of Study B, citing the Werner et al. two-auditor precedent
and Guest et al. 2006 (basic metathemes present by ~6 interviews). We claim
formative validation, never saturation.

**Recruitment channels:** professional networks of the authors (incl. contacts
introduced via Aziz's future employer — introductions only; the employer has
no role in design, analysis, or reporting) and Alina's regulatory/standards
network. Participation is personal, voluntary, unpaid, and independent of the
participants' employers; no employer names are collected as data.

## 3. Study A — catalogue realism review

**Goal.** Establish whether the eight pre-registered violation types (V1–V8)
are recognisable, plausible failure modes to practitioners *before* the
catalogue is frozen — and elicit failure modes we missed.

**Format.** 30–45 min, remote video call (alternative: asynchronous written
review). Semi-structured, instrument-led (Myers & Newman 2007).

**Materials given to the participant** (≥ 3 days ahead): a 2–3-page plain-
language scenario description; the V1–V8 catalogue in plain language; one
rendered example evidence trace. No code.

**Instrument (per violation V1–V8):**

| Item | Scale |
|---|---|
| A1. Plausibility: "Have you seen this failure mode, or a close analogue, in practice?" | yes / similar / no + free text |
| A2. Expected frequency in a real deployment | 5-point (very rare … common) |
| A3. Severity / materiality if found in an audit | 5-point (negligible … critical) + free text |
| A4. Is the *description* faithful to how this would appear in practice? | free text |

**Closing elicitation (verbatim, always asked):**
"*Which realistic failure modes are missing from this list?*" — answers are
the source pool for the **held-out fault set** used to harden the detection
evaluation (authored after template freeze, firewalled from the template
author; see PLAN-REVIEW.md B2).

**Outputs.** Completed instruments + notes/transcript → summarised in
`data/catalogue/REVIEW.md` (protocol version, anonymised roles/experience,
date, duration, per-item ratings, decisions taken). The catalogue is amended
*only before* the freeze; every amendment is traceable to a review comment.

## 4. Study B — evidence inspectability walkthrough (E4)

**Goal.** Can a practitioner independently understand, re-derive, and defend a
violation from its evidence record? (Claim C4's human half — formative.)

**Format.** ~60 min, remote, screen-shared. **Task-based concurrent
think-aloud** (established for expert evaluation of AI evidence: think-aloud
with domain experts on explainable decision support, PLOS ONE
10.1371/journal.pone.0291443; concurrent vs retrospective meta-analysis, ACM
10.1145/3665327).

**Tasks (per participant, 2 violations drawn from different classes):**

| Task | Measure |
|---|---|
| T1. Given the evidence record and the log excerpt, explain in your own words *what* was violated, *by whom*, and *how you know* | success (independent re-derivation, rated against a rubric), time, self-rated confidence (5-pt) |
| T2. Execute the record's rerun command (facilitated; participant observes and verifies) and check the record hash matches | success / failure; comprehension probe: "what did this just prove?" |
| T3. Defensibility judgment: "Would this record survive challenge by the audited firm? What would you attack?" | 5-pt rating + mandatory free-text reasons |
| Post-session | adapted Explanation-Satisfaction items (Hoffman et al.); open feedback |

**Claims this study may support:** existence and formative claims only —
"participants could / could not re-derive violations"; "the defensibility
concerns raised were X, Y". **Claims it may not support:** proportions,
saturation, generalisation to the auditor population, or "auditors accept
this as audit evidence."

## 5. Analysis

Qualitative data (think-aloud transcripts, free text): **reflexive thematic
analysis per Braun & Clarke (2006)**, followed explicitly (familiarisation →
coding → theme construction → review → report); one named method, no hybrid.
(If Alina prefers the German-language tradition, Mayring's qualitative content
analysis is the sanctioned alternative — choose one and state it.)
**Member checking:** each participant receives their session summary and any
quotation slated for use, for correction and approval. Credibility reported
against Lincoln & Guba's criteria; study reporting structured per Runeson &
Höst (2009). Quantitative items (ratings, task success, times) are reported
descriptively with exact counts — no inferential statistics at this n.

## 6. Ethics, data protection, consent

- **Ethics.** Professional-opinion interviews without sensitive personal data;
  formal Ethikkommission approval is typically not required in Germany
  (KonsortSWD guidance), but a **documented ethics self-assessment** is filed
  with the study materials and an ethics statement appears in the paper.
  Alina confirms the applicable institutional policy and checks the
  **April 2026 EDPB guidelines** on scientific-research processing.
- **GDPR.** Recordings and transcripts of identifiable professionals are
  personal data. Lawful basis: **informed consent, Art. 6(1)(a) GDPR**,
  obtained in writing before any recording. Audio is deleted after
  transcription; transcripts are pseudonymised at creation; the code list is
  stored separately and deleted at project end (stated retention period).
- **Consent covers, explicitly and separately checkable:** participation;
  audio recording; transcription and pseudonymised storage; use of anonymised,
  paraphrased findings in (a) the scientific paper, (b) **conference
  presentations, including the Munich Tech Expo talk (20 Sep 2026)**, and
  (c) the published research artifacts; verbatim quotation *only* of
  member-checked, approved quotes. Withdrawal possible until publication.
- **Anonymisation commitment** (also the recruiting promise): no names, no
  employer names; roles described generically ("senior internal auditor,
  German bank"); statements paraphrased or aggregated unless a verbatim quote
  is individually approved.
- **Optional named acknowledgment:** independent of data anonymity, each
  participant may *opt in* to being thanked by name in the paper's
  acknowledgments (default: not named). Participants receive the accepted
  paper ahead of publication.
- GDPR consent and research-ethics consent are kept conceptually distinct in
  the form.

## 7. Roles and independence

- **Alina Hafner** — study lead: instrument finalisation, session moderation
  (or co-moderation), analysis, member checking.
- **Aziz Ketata** — technical facilitation (T2 rerun demonstrations), no
  moderation of defensibility judgments about his own artifacts where
  avoidable; **firewalled** from the held-out fault authoring (PLAN-REVIEW B2).
- Introductions from professional networks create no employer involvement;
  this is stated in the information sheet.

### 7a. Firewall procedure (operational)

Rationale: the detector author cannot also author the detector's exam —
anything he authors, the detector passes by construction; and any expert
fault he *reads* before the template freeze silently becomes training data.
Because Study A sessions (~21 Aug) precede the Phase 5 template build, the
firewall is on the **answers**, not merely the calendar:

1. Aziz may facilitate the Study A ratings segment (V1–V8 are pre-registered
   ground truth under review, not secret).
2. For the closing elicitation ("which failure modes are missing / what have
   you seen?"), **Aziz leaves the session**; Alina records the answers in a
   store he does not access.
3. Alina + practitioner(s) derive 4–8 concrete held-out fault specifications
   (events to add / modify / delete), kept sealed.
4. Aziz builds and freezes the templates from the article text and the
   original catalogue only; freeze = commit + tag + push to public remote +
   external timestamp (PLAN-REVIEW B9).
5. Only after the freeze do the sealed specs reach Aziz, who implements the
   injectors mechanically; Alina verifies the injected logs match her specs.
6. Held-out recall is reported as the generalization result; this procedure
   is described in the paper, with the freeze artifacts as proof of order.

Session phrasing when a participant asks what the system checks (before the
elicitation): *"We show everything afterwards — first we need your list
uncontaminated by ours; the separation is what makes your input usable as
evidence."*

## 8. Artifacts and timeline

**Published with the paper** (Zenodo, CC-BY 4.0): this protocol, the
instruments, the consent-form template, anonymised aggregated ratings, and
`data/catalogue/REVIEW.md`.

| When | What |
|---|---|
| by 19 Aug | Consent form + information sheet finalised (Alina) |
| ~21 Aug | Study A sessions (first participant[s]) |
| by 28 Aug | Study B pilot materials ready (evidence records exist after Phase 6) |
| 1–8 Sep | Study B formative pilot (n = 2–3) — *decision gate 4 Sep: if no session is scheduled by then, descope C4's human half per PLAN-REVIEW B3* |
| 4 / 11 Sep | Abstract / paper (ICPM 2027) |
| post-notification | Full Study B rounds for camera-ready (25 Nov) / follow-up work |

## 9. Method references

Hevner, March, Park, Ram (2004) MISQ · Peffers et al. (2007) JMIS · Peffers
et al. (2012) DESRIST (artifact–method pairing) · Wieringa (2014) *Design
Science Methodology* · Myers & Newman (2007) *The qualitative interview in IS
research* · Braun & Clarke (2006) *Using thematic analysis in psychology* ·
Mayring (2014) *Qualitative Content Analysis* · Lincoln & Guba (1985)
*Naturalistic Inquiry* · Runeson & Höst (2009) EMSE case-study guidelines ·
Guest, Bunce & Johnson (2006) Field Methods · Werner et al., *Embedding
process mining into financial statement audits* · think-aloud XAI precedent:
PLOS ONE 10.1371/journal.pone.0291443; ACM 10.1145/3665327.

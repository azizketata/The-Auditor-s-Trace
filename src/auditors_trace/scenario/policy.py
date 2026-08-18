"""Pure, deterministic credit assessment and approval logic.

No I/O, no LLM, no wall clock, no randomness beyond a seeded hash. This is
what makes the scripted fleet's outputs vary realistically across sessions
instead of cycling through canned strings: every score, outcome, reason code,
and message is a deterministic function of the real German Credit record.

Two invariants, both asserted by tests:

- ``assess`` never reads ``ground_truth_risk`` (the dataset label) — that
  would make the whole scenario circular.
- ``assess`` never reads the protected-attribute fields (sex, marital status,
  age, foreign worker). Those exist only for the bias-monitoring resource,
  under the AI Act Art. 10(2)(f)-(g) bias-examination duty. No reason code
  mentions them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from auditors_trace.scenario.catalogue import ApproverSpec, PolicyVersionSpec
from auditors_trace.scenario.seed import Applicant

#: Fields the scorecard must never touch. Tested by mutation.
PROTECTED_FIELDS: Final[frozenset[str]] = frozenset(
    {"sex", "marital_status", "age_years", "foreign_worker", "ground_truth_risk"}
)

#: Reason code -> natural language, for evidence records. None mentions a
#: protected attribute; that is deliberate and tested.
REASON_CODES: Final[Mapping[str, str]] = {
    "RC01": "checking account absent or persistently negative",
    "RC02": "credit history shows past payment delays or a critical account",
    "RC03": "savings insufficient relative to requested amount",
    "RC04": "employment tenure below policy minimum",
    "RC05": "installment burden high relative to disposable income",
    "RC06": "requested amount large for the product class",
    "RC07": "requested term long for the product class",
    "RC08": "no realisable property or collateral",
    "RC09": "existing credit obligations elsewhere",
    "RC10": "reliance on co-applicant without guarantor standing",
    "RC98": "human approver declined to endorse the automated outcome; referred for manual review",
    "RC99": "aggregate score below approval threshold",
}

GRANT_THRESHOLD: Final[int] = 500
REFER_THRESHOLD: Final[int] = 455

#: Share of sessions in which the human approver overrides the agent.
OVERRIDE_RATE: Final[float] = 0.08


@dataclass(frozen=True, slots=True)
class Assessment:
    """The scorecard verdict for one application."""

    score: int
    band: str
    outcome: str  # grant | refer | deny
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """The simulated human approval step's verdict."""

    approver_id: str
    approver_role: str
    authority_level: int
    granted: bool
    rationale: str


def rng_key(seed: int, application_id: str, purpose: str) -> str:
    """A stable key for one deterministic random draw."""
    return f"{seed}:{application_id}:{purpose}"


def rng_unit(key: str) -> float:
    """Map a key to [0, 1), deterministically and platform-independently."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _score_components(a: Applicant) -> dict[str, int]:
    """Per-feature score deltas. Keys are reason codes for negative rules."""
    checking = {
        "lt_0_dm": -55,
        "0_to_200_dm": -20,
        "ge_200_dm_or_salary": 25,
        "no_checking_account": 45,
    }[a.checking_status]
    history = {
        "no_credits_all_paid": 15,
        "all_paid_this_bank": 15,
        "existing_paid_duly": 10,
        "delay_in_past": -35,
        "critical_other_credits": -20,
    }[a.credit_history]
    savings = {
        "lt_100_dm": -20,
        "100_to_500_dm": -10,
        "500_to_1000_dm": 5,
        "ge_1000_dm": 25,
        "unknown_or_none": 5,
    }[a.savings]
    employment = {
        "unemployed": -35,
        "lt_1_year": -20,
        "1_to_4_years": 5,
        "4_to_7_years": 15,
        "ge_7_years": 25,
    }[a.employed_since]
    installment = -8 * (a.installment_rate_pct - 1)
    amount = -6 * (a.credit_amount_dm // 2000)
    duration = -9 * (a.duration_months // 12)
    prop = {
        "real_estate": 20,
        "building_savings_or_insurance": 10,
        "car_or_other": 0,
        "unknown_or_none": -20,
    }[a.property_type]
    debtors = {"none": 0, "co_applicant": -20, "guarantor": 15}[a.other_debtors]
    plans = {"bank": -15, "stores": -10, "none": 5}[a.other_installment_plans]
    existing = -8 * (a.existing_credits - 1)
    housing = {"rent": -5, "own": 10, "for_free": -5}[a.housing]
    return {
        "RC01": checking,
        "RC02": history,
        "RC03": savings,
        "RC04": employment,
        "RC05": installment,
        "RC06": amount,
        "RC07": duration,
        "RC08": prop,
        "RC10": debtors,
        "RC09": plans + existing,
        "_housing": housing,
    }


def assess(a: Applicant, policy: PolicyVersionSpec) -> Assessment:
    """Score one application under the given policy version.

    Reads only policy features — never the dataset label, never the Art. 9
    proxy block. Reason codes are the ids of the negative rules that fired,
    and are non-empty for every adverse outcome by construction.
    """
    components = _score_components(a)
    score = 500 + sum(components.values())

    if score >= GRANT_THRESHOLD:
        outcome, band = "grant", "prime" if score >= 580 else "standard"
    elif score >= REFER_THRESHOLD:
        outcome, band = "refer", "review"
    else:
        outcome, band = "deny", "subprime"

    codes = tuple(
        sorted(
            code for code, delta in components.items() if not code.startswith("_") and delta <= -20
        )
    )
    if outcome != "grant" and not codes:
        codes = ("RC99",)
    if outcome == "grant":
        codes = ()
    return Assessment(score=score, band=band, outcome=outcome, reason_codes=codes)


def approval_decision(
    assessment: Assessment, amount_dm: int, approver: ApproverSpec, key: str
) -> ApprovalOutcome:
    """The simulated human step: mostly concurs, overrides ~8% of the time.

    A plain scripted gateway, deliberately not ``langgraph.types.interrupt()``:
    ``interrupt`` re-executes the whole node body on resume, which would emit
    every prior span twice — a fatal corruption in an audit log.
    """
    granted = rng_unit(key) >= OVERRIDE_RATE
    if granted:
        rationale = (
            f"Concur with scorecard outcome '{assessment.outcome}' "
            f"(score {assessment.score}, band {assessment.band}) for {amount_dm} DM."
        )
    else:
        rationale = (
            f"Decline to endorse scorecard outcome '{assessment.outcome}' for "
            f"{amount_dm} DM; case escalated for manual review."
        )
    return ApprovalOutcome(
        approver_id=approver.approver_id,
        approver_role=approver.role,
        authority_level=approver.authority_level,
        granted=granted,
        rationale=rationale,
    )


def final_outcome(assessment: Assessment, approval: ApprovalOutcome) -> str:
    """The decision outcome: the scorecard's, unless the human declined."""
    return assessment.outcome if approval.granted else "refer"


def final_reason_codes(assessment: Assessment, approval: ApprovalOutcome) -> tuple[str, ...]:
    """The decision's reason codes, reflecting the *final* outcome.

    A human override adds RC98 — the true principal reason. Without it, an
    overridden grant would carry RC99 ("score below threshold") next to a
    recorded score *above* the threshold: a self-contradicting audit record.
    """
    if approval.granted:
        return assessment.reason_codes
    return tuple(sorted({*assessment.reason_codes, "RC98"}))


def final_decision_statement(outcome: str, score: int, reason_codes: tuple[str, ...]) -> str:
    """The applicant-facing statement for the decision as actually made.

    Distinct from :func:`adverse_action_statement`, which describes the
    scorecard's *proposed* verdict: in override sessions the two legitimately
    differ, and the ``emit_reasoning`` event that explains the final decision
    must hash this text, not the pre-approval draft.
    """
    reasons = "; ".join(f"{c}: {REASON_CODES[c]}" for c in reason_codes)
    return (
        f"Final decision: '{outcome}' (score {score}). "
        f"Principal reasons: {reasons or 'none recorded'}."
    )


def adverse_action_statement(assessment: Assessment) -> str:
    """The applicant-facing statement of principal reasons (Art. 13 / 86)."""
    reasons = "; ".join(f"{c}: {REASON_CODES[c]}" for c in assessment.reason_codes)
    return (
        f"The application was assessed as '{assessment.outcome}' "
        f"(score {assessment.score}). Principal reasons: {reasons or 'none recorded'}."
    )


def scripted_message(
    agent_role: str,
    a: Applicant,
    assessment: Assessment | None,
    facts: Mapping[str, str],
) -> str:
    """The deterministic assistant text the scripted model returns.

    A function of the real applicant record, so the log varies session to
    session without any nondeterminism.
    """
    if agent_role == "intake":
        return (
            f"Application intake summary for {a.applicant_id}: requested "
            f"{a.credit_amount_dm} DM over {a.duration_months} months for "
            f"{a.purpose}. Checking status: {a.checking_status}; savings: "
            f"{a.savings}; employment: {a.employed_since}. Case file opened "
            f"and routed to data retrieval."
        )
    if agent_role == "scoring":
        assert assessment is not None
        fired = ", ".join(assessment.reason_codes) or "no adverse rules"
        return (
            f"Scorecard result for {a.applicant_id}: score {assessment.score}, "
            f"band {assessment.band}, proposed outcome '{assessment.outcome}'. "
            f"Rules fired: {fired}. Demographic attributes were monitored for "
            f"bias only and did not enter the score."
        )
    if agent_role == "adjudication":
        assert assessment is not None
        return (
            f"Decision notice draft for {a.applicant_id}: "
            + adverse_action_statement(assessment)
            + f" Retrieved sources: {facts.get('sources', 'on file')}."
        )
    raise ValueError(f"no scripted message for agent role {agent_role!r}")

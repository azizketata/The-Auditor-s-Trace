"""Real LangChain tools, built per session as closures over the applicant.

Because these are genuine ``BaseTool`` instances invoked through the runnable
path, ``run_type == "tool"`` and ``tool.name`` / ``gen_ai.tool.name`` appear on
layer-B spans exactly as they would in a production fleet.

Every return value is a deterministic function of the applicant record — no
wall clock, no RNG. ``demographic_bias_monitor`` is the only tool that touches
the Art. 9 proxy fields, and it is invoked for bias monitoring, never scoring.
Tools are constructed per session rather than at module import so no state
leaks between sessions — which matters for the 5000-session Phase 8 run.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from auditors_trace.scenario.catalogue import PolicyVersionSpec, ScenarioCatalogue
from auditors_trace.scenario.policy import assess
from auditors_trace.scenario.seed import Applicant, demographic_projection


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def tool_output_digest(payload: str) -> str:
    """A short digest of a tool payload, for span attributes."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_tools(
    catalogue: ScenarioCatalogue,
    applicant: Applicant,
    policy: PolicyVersionSpec,
) -> dict[str, BaseTool]:
    """The five credit-workflow tools, closed over one applicant."""

    @tool
    def credit_bureau_lookup(application_id: str) -> str:
        """Fetch the applicant's external credit bureau record."""
        # A plausible bureau view derived deterministically from the record.
        delinquencies = 1 if applicant.credit_history in ("delay_in_past",) else 0
        open_accounts = applicant.existing_credits
        return _canonical(
            {
                "application_id": application_id,
                "source": "RES-CREDIT-BUREAU",
                "open_accounts": open_accounts,
                "delinquencies_24m": delinquencies,
                "credit_history_class": applicant.credit_history,
                "other_installment_plans": applicant.other_installment_plans,
            }
        )

    @tool
    def income_verification(application_id: str) -> str:
        """Verify declared income and employment against payroll data."""
        return _canonical(
            {
                "application_id": application_id,
                "source": "RES-INCOME",
                "employment_class": applicant.employed_since,
                "job_class": applicant.job,
                "installment_rate_pct_of_income": applicant.installment_rate_pct,
                "verified": applicant.employed_since != "unemployed",
            }
        )

    @tool
    def internal_credit_history(applicant_ref: str) -> str:
        """Fetch the applicant's account history held at this institution."""
        return _canonical(
            {
                "applicant_ref": applicant_ref,
                "source": "RES-INTERNAL-HISTORY",
                "checking_status": applicant.checking_status,
                "savings_class": applicant.savings,
                "existing_credits_here": applicant.existing_credits,
                "residence_since_years": applicant.residence_since,
            }
        )

    @tool
    def demographic_bias_monitor(application_id: str) -> str:
        """Retrieve the demographic projection for bias detection only.

        This is the sole reader of the Art. 9 proxy fields. Its output feeds
        post-market bias monitoring under AI Act Art. 10(2)(f)-(g) and never enters
        the scorecard.
        """
        payload: dict[str, Any] = {
            "application_id": application_id,
            "source": "RES-DEMOGRAPHICS",
            "purpose": "bias_detection_and_correction",
        }
        payload.update(demographic_projection(applicant))
        return _canonical(payload)

    @tool
    def risk_scorecard(features_json: str) -> str:
        """Compute the policy scorecard over the retrieved case file."""
        verdict = assess(applicant, policy)
        return _canonical(
            {
                "policy": f"{policy.policy_id}-{policy.version}",
                "score": verdict.score,
                "band": verdict.band,
                "outcome": verdict.outcome,
                "reason_codes": list(verdict.reason_codes),
            }
        )

    tools: dict[str, BaseTool] = {
        "credit_bureau_lookup": credit_bureau_lookup,
        "income_verification": income_verification,
        "internal_credit_history": internal_credit_history,
        "demographic_bias_monitor": demographic_bias_monitor,
        "risk_scorecard": risk_scorecard,
    }
    known = {t.tool_name for t in catalogue.tools}
    missing = set(tools) - known
    if missing:
        raise ValueError(f"tools {sorted(missing)} not declared in the catalogue")
    return tools

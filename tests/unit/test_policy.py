"""The scorecard is deterministic, label-blind, and Art. 9-proxy-blind.

``test_scoring_never_uses_a_protected_attribute`` is the honesty guarantee
behind the Art. 10(2)(f)-(g) bias-examination framing: demographic attributes
are monitored for bias, never scored. Enforced by mutation, not code review.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from auditors_trace.scenario.catalogue import ApproverSpec, PolicyVersionSpec
from auditors_trace.scenario.policy import (
    PROTECTED_FIELDS,
    REASON_CODES,
    Assessment,
    adverse_action_statement,
    approval_decision,
    assess,
    final_decision_statement,
    final_outcome,
    final_reason_codes,
    rng_key,
    rng_unit,
    scripted_message,
)
from auditors_trace.scenario.seed import load_applicants

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"
POLICY = PolicyVersionSpec("POL-CREDIT", "2026.02", "2026-02-01T00:00:00Z", "", True)
APPROVER = ApproverSpec("APR-001", "credit_officer", 1, 5000)

_PROTECTED_MUTATIONS: dict[str, list[object]] = {
    "sex": ["male", "female"],
    "marital_status": ["single", "married_widowed", "divorced_separated"],
    "age_years": [19, 42, 75],
    "foreign_worker": [True, False],
    "ground_truth_risk": ["good", "bad"],
}


def test_scoring_never_uses_a_protected_attribute() -> None:
    for applicant in load_applicants(FIXTURE, 20, 42):
        baseline = assess(applicant, POLICY)
        for field_name, values in _PROTECTED_MUTATIONS.items():
            for value in values:
                mutated = replace(applicant, **{field_name: value})  # type: ignore[arg-type]
                assert assess(mutated, POLICY) == baseline, (
                    f"assess() read protected field {field_name!r}"
                )


def test_protected_fields_cover_the_proxy_block_and_the_label() -> None:
    assert PROTECTED_FIELDS == set(_PROTECTED_MUTATIONS)


def test_no_reason_code_mentions_a_protected_attribute() -> None:
    banned = ("sex", "gender", "marital", "age", "foreign", "national")
    for code, text in REASON_CODES.items():
        assert not any(term in text.lower() for term in banned), (code, text)


def test_adverse_outcomes_always_carry_reason_codes() -> None:
    for applicant in load_applicants(FIXTURE, 20, 42):
        verdict = assess(applicant, POLICY)
        if verdict.outcome in ("deny", "refer"):
            assert verdict.reason_codes, verdict
            assert all(code in REASON_CODES for code in verdict.reason_codes)
        else:
            assert verdict.reason_codes == ()


def test_assessment_is_deterministic() -> None:
    applicant = load_applicants(FIXTURE, 1, 42)[0]
    assert assess(applicant, POLICY) == assess(applicant, POLICY)


def test_rng_unit_is_stable_and_uniform_ish() -> None:
    key = rng_key(42, "APP-1", "approval")
    assert rng_unit(key) == rng_unit(key)
    draws = [rng_unit(rng_key(42, f"APP-{i}", "approval")) for i in range(2000)]
    assert all(0.0 <= d < 1.0 for d in draws)
    assert 0.4 < sum(draws) / len(draws) < 0.6


def _key_with_draw(*, below_rate: bool) -> str:
    """Deterministically search for a key whose draw is below/above the rate."""
    from auditors_trace.scenario.policy import OVERRIDE_RATE

    for i in range(10_000):
        key = f"search-{i}"
        if (rng_unit(key) < OVERRIDE_RATE) is below_rate:
            return key
    raise AssertionError("no key found — OVERRIDE_RATE degenerate?")


def test_override_flips_outcome_to_refer() -> None:
    applicant = load_applicants(FIXTURE, 1, 42)[0]
    verdict = assess(applicant, POLICY)
    declined = approval_decision(verdict, 1000, APPROVER, _key_with_draw(below_rate=True))
    granted = approval_decision(verdict, 1000, APPROVER, _key_with_draw(below_rate=False))
    assert declined.granted is False
    assert granted.granted is True
    assert final_outcome(verdict, declined) == "refer"
    assert final_outcome(verdict, granted) == verdict.outcome


def test_final_reason_codes_record_the_override_not_a_contradiction() -> None:
    # An overridden grant must cite RC98 (the human override), never RC99
    # ("score below threshold") next to a recorded score above the threshold.
    grant_verdict = Assessment(score=607, band="prime", outcome="grant", reason_codes=())
    declined = approval_decision(grant_verdict, 1000, APPROVER, _key_with_draw(below_rate=True))
    codes = final_reason_codes(grant_verdict, declined)
    assert codes == ("RC98",)
    granted = approval_decision(grant_verdict, 1000, APPROVER, _key_with_draw(below_rate=False))
    assert final_reason_codes(grant_verdict, granted) == ()
    statement = final_decision_statement("refer", 607, codes)
    assert "RC98" in statement
    assert "refer" in statement


def test_scripted_messages_vary_with_the_applicant() -> None:
    a, b = load_applicants(FIXTURE, 2, 42)
    assert scripted_message("intake", a, None, {}) != scripted_message("intake", b, None, {})
    verdict = assess(a, POLICY)
    text = adverse_action_statement(verdict)
    for code in verdict.reason_codes:
        assert code in text


def test_scripted_text_never_leaks_the_label_or_protected_fields() -> None:
    # The dataset label and the Art. 9 proxies must not reach the emitted
    # trace text either — mutation-tested, mirroring the assess() guarantee.
    for applicant in load_applicants(FIXTURE, 5, 42):
        verdict = assess(applicant, POLICY)
        base_intake = scripted_message("intake", applicant, None, {})
        base_scoring = scripted_message("scoring", applicant, verdict, {})
        base_adjudication = scripted_message("adjudication", applicant, verdict, {})
        for field_name, values in _PROTECTED_MUTATIONS.items():
            for value in values:
                mutated = replace(applicant, **{field_name: value})  # type: ignore[arg-type]
                assert scripted_message("intake", mutated, None, {}) == base_intake
                assert scripted_message("scoring", mutated, verdict, {}) == base_scoring
                assert scripted_message("adjudication", mutated, verdict, {}) == base_adjudication

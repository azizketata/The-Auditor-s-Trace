"""The scripted chat model records provenance without leaking the script."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from auditors_trace.scenario.catalogue import ModelSpec, scripted_model_spec
from auditors_trace.scenario.models import (
    ProviderUnavailableError,
    ScriptedCreditModel,
    build_chat_model,
    token_estimate,
)

ANTHROPIC_SPEC = ModelSpec(
    model_id="claude-haiku-4-5",
    provider="anthropic",
    version="claude-haiku-4-5-20251001",
    temperature=0.0,
    seed=42,
    max_tokens=512,
)


def _scripted() -> ScriptedCreditModel:
    spec = scripted_model_spec(ANTHROPIC_SPEC)
    model = build_chat_model(spec, "The decision notice text.")
    assert isinstance(model, ScriptedCreditModel)
    return model


def test_scripted_spec_never_impersonates_a_vendor_model() -> None:
    spec = scripted_model_spec(ANTHROPIC_SPEC)
    assert spec.provider == "scripted"
    assert "claude" not in spec.model_id
    assert spec.temperature == ANTHROPIC_SPEC.temperature
    assert spec.seed == ANTHROPIC_SPEC.seed


def test_identifying_params_carry_provenance_but_not_the_script() -> None:
    model = _scripted()
    params = model._identifying_params
    assert params["temperature"] == 0.0
    assert params["seed"] == 42
    assert params["model"] == "scripted-credit-policy-v1"
    assert "script" not in params
    assert "The decision notice" not in str(params.values())


def test_generate_returns_the_script_with_usage_metadata() -> None:
    model = _scripted()
    message = model.invoke([HumanMessage(content="case file")])
    assert message.content == "The decision notice text."
    usage = getattr(message, "usage_metadata", None)
    assert usage is not None
    assert usage["input_tokens"] >= 1
    assert usage["output_tokens"] == token_estimate("The decision notice text.")


def test_anthropic_without_key_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailableError, match="ANTHROPIC_API_KEY"):
        build_chat_model(ANTHROPIC_SPEC, "unused")


def test_unknown_provider_raises() -> None:
    spec = ModelSpec("x", "openai", "1", 0.0, 0, 16)
    with pytest.raises(ProviderUnavailableError, match="unknown provider"):
        build_chat_model(spec, "unused")

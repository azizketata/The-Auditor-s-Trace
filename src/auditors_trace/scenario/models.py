"""The LLM layer and the provider swap point.

``build_chat_model`` is the single seam between the published artefact (real
Anthropic calls, ``provider="anthropic"``) and the hermetic test suite
(``provider="scripted"``). Both go through the real LangChain runnable path,
so the OpenInference instrumentor emits genuine LLM spans either way — with
``llm.model_name``, ``llm.provider``, ``llm.invocation_parameters`` and token
counts. Only the token *content* differs.

``ScriptedCreditModel`` is deliberately not a subclass of LangChain's
``FakeListChatModel``: that class cycles a mutable response index (call-order
dependent) and leaks its whole response list into ``_identifying_params`` —
which flows verbatim into ``llm.invocation_parameters`` on every span.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LangSmithParams
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult

from auditors_trace.scenario.catalogue import ModelSpec


class ProviderUnavailableError(RuntimeError):
    """The requested LLM provider cannot be used in this environment."""


def token_estimate(text: str) -> int:
    """A deterministic, provider-independent token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


class ScriptedCreditModel(BaseChatModel):
    """A deterministic chat model whose one response is set at construction.

    Stateless by design: ``build_chat_model`` returns a fresh instance per
    call with ``script`` pre-set, so there is no response-cycling index and no
    dependence on call order. ``temperature`` and ``seed`` are recorded
    provenance — with ``provider="scripted"`` the determinism comes from the
    script itself, not from sampling parameters.
    """

    model: str
    provider: str = "scripted"
    version: str = "1.0.0"
    temperature: float = 0.0
    seed: int = 0
    max_tokens: int = 512
    script: str

    @property
    def _llm_type(self) -> str:
        return "scripted-credit"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        # Everything here lands in llm.invocation_parameters on every span.
        # The script must NOT be included — it would leak the entire response
        # text into the invocation parameters of the telemetry.
        return {
            "model": self.model,
            "provider": self.provider,
            "version": self.version,
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
        }

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> LangSmithParams:
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = "scripted"
        params["ls_model_name"] = self.model
        params["ls_temperature"] = self.temperature
        params["ls_max_tokens"] = self.max_tokens
        return params

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt_text = "\n".join(str(m.content) for m in messages)
        input_tokens = token_estimate(prompt_text)
        output_tokens = token_estimate(self.script)
        # A content-derived message id: without one, LangChain stamps the
        # run UUID onto the message, which would be the only nondeterministic
        # byte in the scripted model's own output.
        digest = hashlib.sha256((prompt_text + "\x00" + self.script).encode()).hexdigest()[:16]
        message = AIMessage(
            content=self.script,
            id=f"msg-{digest}",
            usage_metadata=UsageMetadata(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def build_chat_model(spec: ModelSpec, script: str) -> BaseChatModel:
    """Return a fresh chat model for one call.

    ``provider="scripted"`` returns the deterministic model above;
    ``provider="anthropic"`` returns a real ``ChatAnthropic`` (the ``script``
    argument is ignored). Anything else, or a missing key/package, raises
    :class:`ProviderUnavailableError` with an actionable message.
    """
    if spec.provider == "scripted":
        return ScriptedCreditModel(
            model=spec.model_id,
            provider=spec.provider,
            version=spec.version,
            temperature=spec.temperature,
            seed=spec.seed,
            max_tokens=spec.max_tokens,
            script=script,
        )
    if spec.provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderUnavailableError(
                "provider 'anthropic' requires ANTHROPIC_API_KEY in the environment; "
                "set it, or run with --provider scripted"
            )
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ProviderUnavailableError(
                "provider 'anthropic' requires the langchain-anthropic package; "
                "install the [scenario] extra (uv sync --all-extras)"
            ) from exc
        return ChatAnthropic(
            model_name=spec.model_id,
            temperature=spec.temperature,
            max_tokens_to_sample=spec.max_tokens,
            timeout=60,
            stop=None,
        )
    raise ProviderUnavailableError(f"unknown provider {spec.provider!r}")

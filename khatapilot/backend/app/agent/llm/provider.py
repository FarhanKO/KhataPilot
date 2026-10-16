"""Choose the LLM provider from settings (`LLM_MODE`)."""

from __future__ import annotations

from app.agent.llm.types import LLMProvider
from app.core.config import Settings


def make_provider(settings: Settings) -> LLMProvider:
    if settings.llm_mode == "anthropic":
        from app.agent.llm.anthropic_provider import AnthropicProvider

        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        return AnthropicProvider(model=settings.llm_model, api_key=key, effort=settings.llm_effort,
                                 fallbacks=settings.llm_fallbacks, timeout=settings.llm_timeout_seconds)
    if settings.llm_mode == "openai_compatible":
        from app.agent.llm.openai_provider import OpenAICompatibleProvider

        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        return OpenAICompatibleProvider(model=settings.openai_model, api_key=key, base_url=settings.openai_base_url,
                                        timeout=settings.llm_timeout_seconds)
    from app.agent.llm.mock_provider import MockProvider

    return MockProvider()

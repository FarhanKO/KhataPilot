"""Claude through the official Anthropic SDK.

Choices that matter on current models (Claude Sonnet 5.5 is the default, `LLM_MODEL`):
- adaptive thinking (the default) with an explicit effort (`LLM_EFFORT`, default `medium`, the starting
  point for multistep tool use); no `disabled` thinking, no budgets;
- `tool_choice` stays `auto` (forced tool choice is rejected on these models); the guard validates
  every argument anyway;
- each turn is append-only: the assistant's content is passed back exactly as returned, including
  thinking blocks, and earlier turns are carried as text only, so the conversation prefix never
  changes under a thinking block and the prompt cache stays warm;
- `stop_reason == "refusal"` is checked before reading content, and server-side refusal fallback
  (`fallbacks: "default"`) is on for the models that support it on the Claude API (`LLM_FALLBACKS`).
"""

from __future__ import annotations

import base64
import json
from typing import Any

import anthropic

from app.agent.llm.types import HistoryMessage, LLMError, LLMStep, LLMTool, LLMToolCall, ToolResult, TurnBrief

MAX_TOKENS = 16_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

# List prices in US$ per million tokens (input, output), used only to report what a live run cost.
# Cache writes cost 1.25x input and cache reads 0.1x input.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
USAGE_FIELDS = ("calls", "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def estimate_cost(model: str, usage: dict[str, int]) -> float | None:
    """Rough US$ cost of the tokens in `usage` at list prices; None for a model without a price here."""
    price = PRICES.get(model)
    if price is None:
        return None
    per_in, per_out = price[0] / 1e6, price[1] / 1e6
    return (usage.get("input_tokens", 0) * per_in + usage.get("output_tokens", 0) * per_out
            + usage.get("cache_creation_input_tokens", 0) * per_in * 1.25
            + usage.get("cache_read_input_tokens", 0) * per_in * 0.1)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, model: str, api_key: str | None, effort: str = "medium", fallbacks: bool = True,
                 timeout: float = 60.0, client: Any = None) -> None:
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks and model in FALLBACK_MODELS
        self.usage = dict.fromkeys(USAGE_FIELDS, 0)  # running totals for this provider, for cost reports
        if client is not None:
            self.client = client
        elif api_key:
            self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        else:  # ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile
            self.client = anthropic.Anthropic(timeout=timeout, max_retries=2)

    def create(self, *, effort: str | None = None, output_format: dict[str, Any] | None = None, **params: Any):
        output_config: dict[str, Any] = {"effort": effort or self.effort}
        if output_format:
            output_config["format"] = output_format
        request = dict(model=self.model, max_tokens=MAX_TOKENS, output_config=output_config, **params)
        if self.fallbacks:
            request.update(betas=[FALLBACK_BETA], fallbacks="default")
        try:
            response = self.client.beta.messages.create(**request)
            self.usage["calls"] += 1
            for name in USAGE_FIELDS[1:]:
                value = getattr(getattr(response, "usage", None), name, 0)
                self.usage[name] += value if isinstance(value, int) else 0
            return response
        except anthropic.AuthenticationError as exc:
            raise LLMError("The AI service did not accept our API key. Check ANTHROPIC_API_KEY in .env.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("This API key is not allowed to use the configured model.") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"The model {self.model!r} was not found. Check LLM_MODEL in .env.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("The AI service is busy right now. Please try again in a minute.") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"The AI service rejected the request ({exc.message}).") from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise LLMError("I could not reach the AI service. Check the internet connection.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"The AI service had an error ({exc.status_code}). Please try again.") from exc

    def session(self, *, system: str, tools: list[LLMTool], history: list[HistoryMessage]) -> AnthropicSession:
        return AnthropicSession(self, system, tools, history)

    def extract(self, *, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        response = self.create(
            effort="low",
            system=system,
            messages=[{"role": "user", "content": text}],
            output_format={"type": "json_schema", "schema": schema},
        )
        if response.stop_reason == "refusal":
            return None
        return _json_body(response)

    def extract_image(self, *, system: str, image: bytes, media_type: str,
                      schema: dict[str, Any]) -> dict[str, Any] | None:
        data = base64.standard_b64encode(image).decode("ascii")
        response = self.create(
            effort="low",
            system=system,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": "Copy the text in this photo."},
            ]}],
            output_format={"type": "json_schema", "schema": schema},
        )
        if response.stop_reason == "refusal":
            return None
        return _json_body(response)


def _json_body(response: Any) -> dict[str, Any] | None:
    body = next((block.text for block in response.content if block.type == "text"), None)
    try:
        return json.loads(body) if body else None
    except json.JSONDecodeError:
        return None


class AnthropicSession:
    def __init__(self, provider: AnthropicProvider, system: str, tools: list[LLMTool],
                 history: list[HistoryMessage]) -> None:
        self.provider = provider
        self.system = [{"type": "text", "text": system}]
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        self.messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in history]

    def user(self, content: str, brief: TurnBrief) -> None:
        self.messages.append({"role": "user", "content": content})

    def step(self) -> LLMStep:
        response = self.provider.create(system=self.system, tools=self.tools, messages=self.messages,
                                        cache_control={"type": "ephemeral"})
        # Pass the assistant turn back exactly as returned (thinking blocks included).
        self.messages.append({"role": "assistant", "content": response.content})
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        }
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return LLMStep(text="", tool_calls=[], stop_reason="refusal", model=response.model, usage=usage,
                           refusal_category=getattr(details, "category", None) if details else None)
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        calls = [LLMToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                 for block in response.content if block.type == "tool_use"]
        return LLMStep(text=text, tool_calls=calls, stop_reason=response.stop_reason or "end_turn",
                       model=response.model, usage=usage)

    def tool_results(self, results: list[ToolResult]) -> None:
        # All results of one step go back in a single user message.
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
            for r in results
        ]})

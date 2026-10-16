"""Any OpenAI-compatible chat-completions endpoint (`LLM_MODE=openai_compatible`), so the team can
switch providers without touching the agent or the guard."""

from __future__ import annotations

import json
from typing import Any

import openai

from app.agent.llm.types import HistoryMessage, LLMError, LLMStep, LLMTool, LLMToolCall, ToolResult, TurnBrief

MAX_TOKENS = 4_000
STOP_REASONS = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None = None, timeout: float = 60.0,
                 client: Any = None) -> None:
        self.model = model
        self.client = client or openai.OpenAI(api_key=api_key or "not-set", base_url=base_url or None,
                                              timeout=timeout, max_retries=2)

    def create(self, **params: Any):
        try:
            return self.client.chat.completions.create(model=self.model, max_tokens=MAX_TOKENS, **params)
        except openai.AuthenticationError as exc:
            raise LLMError("The AI service did not accept our API key. Check OPENAI_API_KEY in .env.") from exc
        except openai.RateLimitError as exc:
            raise LLMError("The AI service is busy right now. Please try again in a minute.") from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise LLMError("I could not reach the AI service. Check OPENAI_BASE_URL and the connection.") from exc
        except openai.APIError as exc:
            raise LLMError(f"The AI service had an error ({type(exc).__name__}). Please try again.") from exc

    def session(self, *, system: str, tools: list[LLMTool], history: list[HistoryMessage]) -> OpenAISession:
        return OpenAISession(self, system, tools, history)

    def extract(self, *, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        response = self.create(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            response_format={"type": "json_schema", "json_schema": {"name": "extraction", "schema": schema,
                                                                    "strict": True}},
        )
        body = response.choices[0].message.content
        try:
            return json.loads(body) if body else None
        except json.JSONDecodeError:
            return None

    def extract_image(self, *, system: str, image: bytes, media_type: str,
                      schema: dict[str, Any]) -> dict[str, Any] | None:
        raise LLMError("Reading invoice photos needs LLM_MODE=anthropic (or mock for the demo samples).")


class OpenAISession:
    def __init__(self, provider: OpenAICompatibleProvider, system: str, tools: list[LLMTool],
                 history: list[HistoryMessage]) -> None:
        self.provider = provider
        self.tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                         "parameters": t.parameters}} for t in tools]
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        self.messages += [{"role": m.role, "content": m.content} for m in history]

    def user(self, content: str, brief: TurnBrief) -> None:
        self.messages.append({"role": "user", "content": content})

    def step(self) -> LLMStep:
        response = self.provider.create(messages=self.messages, tools=self.tools, tool_choice="auto")
        choice = response.choices[0]
        message = choice.message
        calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"unparseable_arguments": call.function.arguments}
            calls.append(LLMToolCall(id=call.id, name=call.function.name, arguments=arguments))
        self.messages.append({
            "role": "assistant",
            "content": message.content,
            **({"tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name, "arguments": c.function.arguments}}
                               for c in message.tool_calls]} if message.tool_calls else {}),
        })
        usage = getattr(response, "usage", None)
        return LLMStep(
            text=(message.content or "").strip(),
            tool_calls=calls,
            stop_reason=STOP_REASONS.get(choice.finish_reason, "end_turn"),
            model=getattr(response, "model", self.provider.model),
            usage={"input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                   "output_tokens": getattr(usage, "completion_tokens", 0) or 0} if usage else {},
        )

    def tool_results(self, results: list[ToolResult]) -> None:
        for result in results:
            self.messages.append({"role": "tool", "tool_call_id": result.call_id, "content": result.content})

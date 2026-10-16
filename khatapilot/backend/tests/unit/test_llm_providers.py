"""The Claude and OpenAI-compatible providers, against fake SDK clients (no network, no key)."""

from types import SimpleNamespace as NS

import anthropic
import httpx2
import openai
import pytest

from app.agent.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from app.agent.llm.openai_provider import OpenAICompatibleProvider
from app.agent.llm.types import HistoryMessage, LLMError, LLMTool, ToolResult
from app.guard.api import ToolOutcome

TOOLS = [LLMTool("get_balances", "Money on hand.", {"type": "object", "properties": {}})]
OUTCOME = ToolOutcome(call_id="c1", tool="get_balances", status="ok", message="ok", data={"total": "৳10"})


class FakeAnthropic:
    def __init__(self, *responses):
        self.calls, self.responses = [], list(responses)
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})  # snapshot at call time
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def claude_reply(*blocks, stop="end_turn", details=None):
    return NS(content=list(blocks), stop_reason=stop, stop_details=details, model="claude-sonnet-5-5",
              usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=80))


def text(value):
    return NS(type="text", text=value)


def tool_use(call_id, name, arguments):
    return NS(type="tool_use", id=call_id, name=name, input=arguments)


def claude(*responses, model="claude-sonnet-5-5"):
    fake = FakeAnthropic(*responses)
    return AnthropicProvider(model=model, api_key=None, client=fake), fake


class TestAnthropic:
    def test_request_shape(self):
        provider, fake = claude(claude_reply(text("Hi")))
        session = provider.session(system="SYSTEM", tools=TOOLS, history=[HistoryMessage("user", "earlier"),
                                                                           HistoryMessage("assistant", "reply")])
        session.user("now", brief=None)
        step = session.step()
        request = fake.calls[0]
        assert request["model"] == "claude-sonnet-5-5"
        assert request["max_tokens"] == 16_000
        assert request["output_config"] == {"effort": "medium"}
        assert request["betas"] == [FALLBACK_BETA] and request["fallbacks"] == "default"
        assert request["cache_control"] == {"type": "ephemeral"}
        assert request["system"] == [{"type": "text", "text": "SYSTEM"}]
        assert request["tools"] == [{"name": "get_balances", "description": "Money on hand.",
                                     "input_schema": {"type": "object", "properties": {}}}]
        assert "tool_choice" not in request and "thinking" not in request
        assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
        assert (step.text, step.stop_reason, step.usage["cache_read_input_tokens"]) == ("Hi", "end_turn", 80)

    def test_fallbacks_only_on_models_that_support_them(self):
        provider, fake = claude(claude_reply(text("Hi")), model="claude-haiku-5-5")
        session = provider.session(system="S", tools=[], history=[])
        session.user("hello", brief=None)
        session.step()
        assert "fallbacks" not in fake.calls[0] and "betas" not in fake.calls[0]

    def test_the_turn_stays_append_only(self):
        first = claude_reply(NS(type="thinking", thinking="", signature="sig"), tool_use("t1", "get_balances", {}),
                             stop="tool_use")
        provider, fake = claude(first, claude_reply(text("You have ৳10.")))
        session = provider.session(system="S", tools=TOOLS, history=[])
        session.user("balance?", brief=None)
        step = session.step()
        assert [(c.id, c.name, c.arguments) for c in step.tool_calls] == [("t1", "get_balances", {})]
        session.tool_results([ToolResult("t1", '{"status": "ok"}', False, OUTCOME)])
        assert session.step().text == "You have ৳10."
        sent = fake.calls[1]["messages"]
        assert sent[1] == {"role": "assistant", "content": first.content}, "assistant content passed back verbatim"
        assert sent[2] == {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"status": "ok"}', "is_error": False}]}

    def test_refusal_is_checked_before_reading_content(self):
        provider, _ = claude(claude_reply(stop="refusal", details=NS(category="cyber")))
        session = provider.session(system="S", tools=[], history=[])
        session.user("x", brief=None)
        step = session.step()
        assert (step.stop_reason, step.refusal_category, step.tool_calls) == ("refusal", "cyber", [])

    @pytest.mark.parametrize(("error", "says"), [
        (anthropic.RateLimitError("slow down", response=httpx2.Response(
            429, request=httpx2.Request("POST", "https://api.anthropic.com")), body=None), "busy"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), "could not reach"),
        (anthropic.AuthenticationError("bad key", response=httpx2.Response(
            401, request=httpx2.Request("POST", "https://api.anthropic.com")), body=None), "API key"),
    ])
    def test_errors_become_plain_messages(self, error, says):
        provider, _ = claude(error)
        session = provider.session(system="S", tools=[], history=[])
        session.user("x", brief=None)
        with pytest.raises(LLMError, match=says):
            session.step()

    def test_extraction_uses_structured_output_and_no_tools(self):
        provider, fake = claude(claude_reply(text('{"supplier_name": "Meghna", "total_taka": 3420}')))
        assert provider.extract(system="S", text="invoice", schema={"type": "object"}) == {
            "supplier_name": "Meghna", "total_taka": 3420}
        request = fake.calls[0]
        assert request["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
        assert "tools" not in request

    def test_photo_extraction_sends_the_image_with_no_tools(self):
        provider, fake = claude(claude_reply(text('{"readable": true, "text": "Total: 4,200"}')))
        found = provider.extract_image(system="S", image=b"\x89PNG\r\n\x1a\nrest", media_type="image/png",
                                       schema={"type": "object"})
        assert found == {"readable": True, "text": "Total: 4,200"}
        request = fake.calls[0]
        image, prompt = request["messages"][0]["content"]
        assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                     "data": "iVBORw0KGgpyZXN0"}}
        assert prompt["type"] == "text"
        assert request["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
        assert "tools" not in request

    def test_a_refused_photo_reads_as_nothing(self):
        provider, _ = claude(claude_reply(stop="refusal", details=NS(category=None)))
        assert provider.extract_image(system="S", image=b"x", media_type="image/png", schema={}) is None


class FakeOpenAI:
    def __init__(self, *responses):
        self.calls, self.responses = [], list(responses)
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})  # snapshot at call time
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def openai_reply(content=None, tool_calls=None, finish="stop"):
    message = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(message=message, finish_reason=finish)], model="some-model",
              usage=NS(prompt_tokens=10, completion_tokens=5))


def function_call(call_id, name, arguments):
    return NS(id=call_id, function=NS(name=name, arguments=arguments))


class TestOpenAICompatible:
    def test_tool_calls_and_results(self):
        fake = FakeOpenAI(openai_reply(tool_calls=[function_call("c1", "get_balances", "{}")], finish="tool_calls"),
                          openai_reply(content="You have ৳10."))
        provider = OpenAICompatibleProvider(model="some-model", api_key="k", client=fake)
        session = provider.session(system="SYSTEM", tools=TOOLS, history=[])
        session.user("balance?", brief=None)
        step = session.step()
        assert (step.stop_reason, step.tool_calls[0].name) == ("tool_use", "get_balances")
        assert fake.calls[0]["tool_choice"] == "auto"
        assert fake.calls[0]["tools"][0]["function"]["name"] == "get_balances"
        session.tool_results([ToolResult("c1", '{"status": "ok"}', False, OUTCOME)])
        assert session.step().text == "You have ৳10."
        assert fake.calls[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok"}'}

    def test_broken_json_arguments_reach_the_guard_as_invalid(self):
        fake = FakeOpenAI(openai_reply(tool_calls=[function_call("c1", "propose_payment", "{oops")], finish="tool_calls"))
        session = OpenAICompatibleProvider(model="m", api_key="k", client=fake).session(system="S", tools=[], history=[])
        session.user("x", brief=None)
        assert session.step().tool_calls[0].arguments == {"unparseable_arguments": "{oops"}

    def test_errors_become_plain_messages(self):
        error = openai.RateLimitError("slow", response=httpx2.Response(429, request=httpx2.Request("POST", "https://x")),
                                      body=None)
        session = OpenAICompatibleProvider(model="m", api_key="k", client=FakeOpenAI(error)).session(
            system="S", tools=[], history=[])
        session.user("x", brief=None)
        with pytest.raises(LLMError, match="busy"):
            session.step()

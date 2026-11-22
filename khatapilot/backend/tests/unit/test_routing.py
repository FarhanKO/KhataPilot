"""Rules first, the model for everything else, and the offline planner when the model can't be reached."""

from datetime import date, datetime

import pytest

from app.agent.llm.mock_provider import MockProvider
from app.agent.llm.types import HistoryMessage, LLMError, LLMStep, LLMToolCall, SegmentBrief, TurnBrief
from app.agent.orchestrator import OFFLINE_REPLY, Orchestrator
from app.agent.routing import choose
from app.core.clock import BDT
from tests.unit.test_agent import RecordingPort, ScriptedProvider
from tests.guard_harness import turn

TODAY = date(2026, 10, 12)


def brief(text, *, sender="owner", forwarded=()):
    segments = (SegmentBrief(1, "chat", text, "owner_text", None, False),) + tuple(
        SegmentBrief(i, "forwarded_sms", t, "mobile_money_sms", None, False) for i, t in enumerate(forwarded, start=2))
    return TurnBrief(sender=sender, sender_display="Owner", owner_text=text, segments=segments, today=TODAY,
                     now=datetime(2026, 10, 12, 10, 5, tzinfo=BDT))


@pytest.mark.parametrize("text", [
    "what's my balance?",
    "who owes me money",
    "how much did I sell today",
    "will I run short of cash?",
    "sold 3 bags of rice ৳1,500 cash",
    "Karim paid 500 taka",
    "paid electricity bill 2000",
    "what did you do this week",
])
def test_plain_requests_go_to_the_rules(text):
    assert choose(brief(text), []).planner == "rules", text


@pytest.mark.parametrize("text, why", [
    ("pay Rahim Traders 3000 by bkash", "payments"),
    ("remind everyone who owes me", "messages to customers"),
    ("can I get a loan for Eid stock", "loans"),
    ("what is my credit score", "credit"),
    ("I didn't sell rice for 1500, it was 1200", "a correction"),
    ("should I pay Meghna first or keep cash for rent", "advice"),
    ("why are my sales down this week", "a why"),
    ("what about last week?", "a follow-up"),
    ("sold rice 1500 and dal 800", "two amounts"),
    ("আজকের বিক্রি কত", "Bangla script"),
    ("tell me a joke", "not a phrase the rules know"),
    ("sold 3 bags of rice 1500 cash to the man from the market who comes every friday morning early", "too long"),
])
def test_anything_else_goes_to_the_model(text, why):
    assert choose(brief(text), []).planner == "model", why


def test_forwarded_content_and_strangers_go_to_the_model():
    assert choose(brief("record this", forwarded=["You have received Tk 500 from 01712345678"]), []).planner == "model"
    assert choose(brief("what's my balance?", sender="external"), []).planner == "model"


def test_an_answer_to_the_models_question_goes_to_the_model():
    history = [HistoryMessage("user", "pay Rahim"), HistoryMessage("assistant", "How much should I pay Rahim?")]
    assert choose(brief("sold rice 500"), history).planner == "model"


# ---------------------------------------------------------------- the orchestrator

def answer(text="From the model."):
    return LLMStep(text, [], "end_turn", "claude-x")


def run(provider, text, **options):
    agent = Orchestrator(provider, max_steps=3, rules=MockProvider(), **options)
    events = []
    reply = agent.handle(turn(text), history=[], port=RecordingPort(), shop_name="Rahman Store", on_event=events.append)
    return reply, agent, events


def test_rules_answer_without_calling_the_model():
    model = ScriptedProvider(answer())
    reply, agent, events = run(model, "what's my balance?", rules_first=True)
    assert reply.route == "rules" and reply.model == "mock-gullible-v1"
    assert not hasattr(model, "system"), "the model was never asked"
    assert agent.routes == {"model": 0, "rules": 1, "offline": 0, "capped": 0}
    assert {"type": "llm_route", "route": "rules"}.items() <= events[0].items()


def test_the_model_answers_what_the_rules_dont_know():
    reply, agent, _ = run(ScriptedProvider(answer()), "why are sales down?", rules_first=True)
    assert reply.route == "model" and reply.text == "From the model."


def test_rules_first_is_off_unless_asked():
    reply, _, _ = run(ScriptedProvider(answer()), "what's my balance?")
    assert reply.route == "model" and reply.text == "From the model."


def test_an_unreachable_model_falls_back_to_the_offline_planner():
    model = ScriptedProvider(LLMError("I could not reach the AI service.", transient=True))
    reply, agent, events = run(model, "what's my balance?", offline_fallback=True)
    assert reply.route == "offline" and reply.model == "mock-gullible-v1"
    assert "Nothing was changed" not in reply.text and reply.text.startswith("On hand")
    assert agent.routes["offline"] == 1
    assert any(e["type"] == "llm_route" and e["route"] == "offline" for e in events)


def test_a_setup_error_is_shown_not_hidden():
    model = ScriptedProvider(LLMError("The AI service did not accept our API key."))
    reply, _, _ = run(model, "what's my balance?", offline_fallback=True)
    assert reply.route == "model" and "did not accept our API key" in reply.text and "Nothing was changed" in reply.text


def test_no_fallback_once_the_model_has_acted():
    model = ScriptedProvider(LLMStep("", [LLMToolCall("c1", "get_balances", {})], "tool_use", "claude-x"),
                             LLMError("I could not reach the AI service.", transient=True))
    reply, _, _ = run(model, "what's my balance?", offline_fallback=True)
    assert reply.route == "model" and "Nothing was changed" in reply.text
    assert [o.tool for o in reply.outcomes] == ["get_balances"], "the offline planner did not run it a second time"


def test_no_fallback_unless_asked():
    """Without the offline planner the owner still gets a calm reply, not the service's error text."""
    reply, _, _ = run(ScriptedProvider(LLMError("down", transient=True)), "what's my balance?")
    assert reply.route == "offline" and reply.text == OFFLINE_REPLY and "Nothing was changed" in reply.text
    assert reply.route_reason == "down", "the technical reason goes to the trace"

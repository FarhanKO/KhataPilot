"""The advisor: facts from the books, advice in the model's words only when every number is in the facts,
fixed words otherwise; on request in chat, and once a week in Alerts."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from app.agent.advisor import grounded, write_tips
from app.agent.llm.anthropic_provider import AnthropicProvider
from app.interface.jobs import weekly_advice
from tests.scenarios.test_llm_budget import paid_app

FACTS = {"cash_forecast": {"lowest": "৳3,200", "safety_buffer": "৳5,000"},
         "baki": {"overdue_owed": "৳41,800", "overdue_customers": 6}}


def test_a_tip_may_only_use_numbers_from_the_facts_and_never_orders_a_payment():
    assert grounded({"title": "Cash gets tight", "text": "Cash drops to ৳3,200, under your ৳5,000 buffer."}, FACTS)
    assert grounded({"title": "Collect baki", "text": "6 customers owe ৳41,800 for 14 days or more."}, FACTS)
    assert not grounded({"title": "Collect baki", "text": "Collect ৳45,000 this week."}, FACTS), "invented amount"
    assert not grounded({"title": "Pay", "text": "Send ৳3,200 to Rahim Traders today."}, FACTS), "orders a payment"


class AdvisorClaude:
    """Answers the advisor's structured request with one grounded tip and one with an invented number."""

    def __init__(self, tips: list[dict]) -> None:
        self.tips = tips
        self.calls = 0
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **_kwargs):
        self.calls += 1
        return NS(content=[NS(type="text", text=json.dumps({"tips": self.tips}))], stop_reason="end_turn",
                  stop_details=None, model="claude-sonnet-5-5",
                  usage=NS(input_tokens=900, output_tokens=120, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))


def test_the_models_tips_are_kept_only_when_grounded():
    fake = AdvisorClaude([{"title": "Cash gets tight", "text": "Cash drops to ৳3,200 on Thu, under ৳5,000."},
                          {"title": "Big win", "text": "You will earn ৳90,000 more next month."}])
    tips = write_tips(AnthropicProvider(model="claude-sonnet-5-5", api_key=None, client=fake), FACTS)
    assert [t["title"] for t in tips] == ["Cash gets tight"]
    assert write_tips(AnthropicProvider(model="claude-sonnet-5-5", api_key=None, client=AdvisorClaude([
        {"title": "Big win", "text": "You will earn ৳90,000 more."}])), FACTS) is None


def test_offline_the_weekly_advice_is_in_fixed_words_once_a_week(app):
    assert weekly_advice(app.container)["why"] == "not the advice day yet"  # 12 Oct 2026 is a Monday
    app.container.settings = app.container.settings.model_copy(update={"advice_weekday": 0})
    out = weekly_advice(app.container)
    assert out["posted"] and out["by"] == "fixed words" and out["tips"]
    assert weekly_advice(app.container)["why"] == "already posted this week"
    cash = next(s for s in app.http.get("/api/notifications").json()["segments"] if s["id"] == "cash")
    card = next(i for i in cash["items"] if i["title"].startswith("This week's advice"))
    assert card["priority"] == "important" and "Written by fixed words" in card["body"]


def test_with_a_paid_model_the_advice_is_in_its_words_and_metered(tmp_path, settings, clock, generated_dir):
    from app.guard.advice import advice_facts
    from app.guard.service import Guard

    fake = AdvisorClaude([])
    provider = AnthropicProvider(model="claude-sonnet-5-5", api_key=None, client=fake)
    with paid_app(tmp_path, settings, clock, generated_dir, provider, 2.0) as http:
        container = http.app.state.container
        with container.db.session() as session:
            facts = advice_facts(Guard(session, container.guard_deps))
        lowest = facts["cash_forecast"]["lowest"]
        fake.tips = [{"title": "Watch your cash", "text": f"Cash is forecast to fall to {lowest}."},
                     {"title": "Made up", "text": "Sales will rise by ৳123,457 next week."}]
        container.settings = container.settings.model_copy(update={"advice_weekday": 0})
        out = weekly_advice(container)
        assert out["by"] == "the AI advisor" and [t["title"] for t in out["tips"]] == ["Watch your cash"]
        assert http.get("/api/llm/usage").json()["month"]["calls"] == 1, "the advice call is metered"


def test_asking_for_advice_in_chat(app):
    reply = app.chat("any advice for this week?")["reply"]
    assert reply.startswith("This week: 1.")
    assert any(e["summary"] == "Advice for this week" for e in app.audit("decision"))

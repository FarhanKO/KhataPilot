"""The monthly AI budget against the real Claude API (excluded by default; `pytest -m live`, about US$0.03).

One real turn spends more than a US$0.001 budget, so the next turn is answered by the offline planner."""

from __future__ import annotations

import pytest

from app.agent.llm.anthropic_provider import AnthropicProvider
from app.core.config import Settings
from tests.scenarios.test_llm_budget import _say, paid_app

pytestmark = pytest.mark.live


def test_live_claude_then_the_offline_planner_once_the_budget_is_spent(tmp_path, settings, clock, generated_dir):
    configured = Settings().anthropic_api_key
    key = configured.get_secret_value() if configured else ""
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    provider = AnthropicProvider(model=Settings().llm_model, api_key=key)
    with paid_app(tmp_path, settings, clock, generated_dir, provider, 0.001) as http:
        first = _say(http, "why did my cash go down so much this week?")
        assert first["route"] == "model" and first["model"].startswith("claude"), first
        month = http.get("/api/llm/usage").json()["month"]
        assert month["capped"] and month["calls"] >= 1 and month["spent_usd"] > 0.001
        second = _say(http, "show my bills")
        assert second["route"] == "capped" and "open bills" in second["reply"]
        assert http.get("/api/llm/usage").json()["month"]["calls"] == month["calls"], "no paid call after the cap"
        print(f"\nlive turn: {month['calls']} call(s), {month['tokens']}, US${month['spent_usd']:.4f}")

"""The monthly AI budget: each call to the model is metered for the shop's month, the owner is told at
80% and at 100%, and once it is spent the offline planner answers for free until the month ends."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.llm.anthropic_provider import AnthropicProvider
from app.bootstrap import build_container
from app.guard.capability import CapabilitySigner
from app.infra.sandbox_client import SandboxClient
from app.main import create_app
from sandbox.app import create_app as create_sandbox
from sandbox.config import SandboxSettings
from scripts.seed import seed_backend, seed_guard, seed_sandbox
from tests.scenarios.conftest import ADMIN, KEY, PIN


class FakeClaude:
    """Answers every call with a short text reply of a known size; fails if called more than expected."""

    def __init__(self, replies: int) -> None:
        self.left = replies
        self.calls = 0
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls > self.left:
            raise AssertionError("the model was called after the budget ran out")
        return NS(content=[NS(type="text", text="Your cash went down because of Meghna's bill.")],
                  stop_reason="end_turn", stop_details=None, model="claude-sonnet-5-5",
                  # 2,000 in + 500 out at US$2/US$10 per million = US$0.009 a call
                  usage=NS(input_tokens=2_000, output_tokens=500, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))


@contextmanager
def paid_app(tmp_path, settings, clock, generated_dir, provider, budget_usd: float):
    """The real app on a fresh seeded world, with a paid model and a monthly AI budget."""
    live = settings.model_copy(update={"llm_mode": "anthropic", "llm_monthly_budget_usd": budget_usd})
    sandbox_http = TestClient(create_sandbox(SandboxSettings(
        _env_file=None, sandbox_data_dir=tmp_path / "sandbox", guard_signing_key=SecretStr(KEY),
        sandbox_admin_key=SecretStr(ADMIN), sim_time=clock.now())))
    sandbox_http.__enter__()
    sandbox = SandboxClient("http://testserver", admin_key=ADMIN, http=sandbox_http)
    container = build_container(live, clock=clock, sandbox=sandbox, signer=CapabilitySigner(KEY.encode()),
                                provider=provider)
    seed_backend(container.db, clock, live.shop_id, generated_dir)
    seed_guard(container.db, clock, live, PIN, generated_dir)
    seed_sandbox(sandbox, clock.now(), generated_dir)
    try:
        with TestClient(create_app(live, container)) as http:
            http.headers.update({"Authorization": "Bearer test-owner-token"})
            yield http
    finally:
        sandbox_http.__exit__(None, None, None)
        container.close()


@pytest.fixture
def paid(tmp_path, settings, clock, generated_dir):
    fake = FakeClaude(replies=2)
    provider = AnthropicProvider(model="claude-sonnet-5-5", api_key=None, client=fake)
    with paid_app(tmp_path, settings, clock, generated_dir, provider, 0.01) as http:
        yield http, fake


def _say(http, text: str) -> dict:
    response = http.post("/api/chat", json={"message": text})
    assert response.status_code == 200, response.text
    return response.json()


def _alerts(http) -> list[str]:
    feed = http.get("/api/notifications").json()
    return [i["title"] for s in feed["segments"] if s["id"] == "security" for i in s["items"]]


def test_the_budget_is_metered_warned_and_then_capped(paid):
    http, fake = paid
    first = _say(http, "why did my cash go down so much this week?")  # needs the model, not the rules
    assert first["route"] == "model"
    month = http.get("/api/llm/usage").json()["month"]
    assert month["spent_usd"] == pytest.approx(0.009) and month["warn"] and not month["capped"]
    assert any(t.startswith("AI budget 90% used") for t in _alerts(http))

    _say(http, "why is my profit lower than last month?")
    month = http.get("/api/llm/usage").json()["month"]
    assert month["capped"] and month["spent_usd"] == pytest.approx(0.018)
    assert any(t.startswith("AI budget used up") for t in _alerts(http))

    capped = _say(http, "why did my cash go down so much this week?")
    assert capped["route"] == "capped" and "budget" in capped["route_reason"]
    assert fake.calls == 2  # the third turn never reached the model
    plain = _say(http, "show my bills")  # plain requests still work offline, for free
    assert plain["route"] == "capped" and "open bills" in plain["reply"]

    month = http.get("/api/llm/usage").json()["month"]
    assert month["turns"]["model"] == 2 and month["turns"]["capped"] == 2
    assert month["calls"] == 2 and month["tokens"]["input"] == 4_000
    assert "used up" in month["text"] and "৳" in month["text"]


def test_offline_mode_costs_nothing_and_is_never_capped(app):
    app.chat("show my bills")
    month = app.http.get("/api/llm/usage").json()["month"]
    assert month["offline_only"] and month["spent_usd"] == 0 and not month["capped"]
    assert month["turns"]["rules"] == 1
    assert "no AI cost" in month["text"]


def test_the_cap_lifts_when_the_next_month_starts(tmp_path, settings, clock, generated_dir):
    fake = FakeClaude(replies=3)
    provider = AnthropicProvider(model="claude-sonnet-5-5", api_key=None, client=fake)
    with paid_app(tmp_path, settings, clock, generated_dir, provider, 0.005) as http:
        assert _say(http, "why did my cash go down so much this week?")["route"] == "model"
        assert _say(http, "why did my cash go down so much this week?")["route"] == "capped"
        assert fake.calls == 1
        clock.advance(timedelta(days=31))  # 12 Oct -> 12 Nov: a new month, a new budget
        assert http.get("/api/llm/usage").json()["month"]["spent_usd"] == 0
        assert _say(http, "why did my cash go down so much this week?")["route"] == "model"
        assert fake.calls == 2


def test_photos_are_read_offline_once_the_budget_is_spent(paid):
    http, fake = paid
    _say(http, "why did my cash go down so much this week?")
    _say(http, "why is my profit lower than last month?")
    assert http.get("/api/llm/usage").json()["month"]["capped"]
    photo = base64.b64encode(bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b" a photo the offline reader does not know").decode()
    response = http.post("/api/chat/photo", json={"image_base64": photo})
    assert fake.calls == 2, "a photo read after the cap never reaches the paid model"
    assert response.status_code in (200, 422), response.text
    if response.status_code == 200:
        assert response.json()["model"].startswith("mock")

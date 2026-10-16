"""Shared fixtures. Tests never read the developer's `.env` and always run the LLM in mock mode."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.clock import BDT, SimClock
from app.core.config import Settings
from app.core.ids import use_random_ids

# Monday 12 Oct 2026, 10:00 in Dhaka: the "now" of every demo and test.
DEMO_NOW = datetime(2026, 10, 12, 10, 0, tzinfo=BDT)


@pytest.fixture
def settings(tmp_path, credit_model_path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path / "var",
        owner_token=SecretStr("test-owner-token"),
        llm_mode="mock",
        sim_time=DEMO_NOW,
        risk_model="rules",  # tests opt in to Fraud Sentinel explicitly, so a local model never changes results
        scheduler_interval_seconds=0,
        credit_model_path=credit_model_path,  # trained on the test population, never a developer's local model
    )


@pytest.fixture
def clock() -> SimClock:
    return SimClock(DEMO_NOW, frozen=True)


@pytest.fixture
def client(settings, clock) -> TestClient:
    from app.bootstrap import build_container
    from app.guard.capability import CapabilitySigner
    from app.main import create_app

    container = build_container(settings, clock=clock, signer=CapabilitySigner(b"t" * 48))
    with TestClient(create_app(settings, container)) as test_client:
        yield test_client
    container.close()


@pytest.fixture(autouse=True)
def _random_ids():
    yield
    use_random_ids()


@pytest.fixture
def world(tmp_path, settings, clock):
    """Business DB, audit DB, the real sandbox in-process, a shop with history, payees and a PIN."""
    from tests.guard_harness import build_world

    built, sandbox_http = build_world(tmp_path, settings, clock)
    yield built
    sandbox_http.__exit__(None, None, None)
    built.audit.dispose()
    built.db.dispose()

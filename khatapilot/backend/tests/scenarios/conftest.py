"""The whole app, end to end: the real backend (mock LLM), the real sandbox in-process, and the
demo shop seeded exactly as `scripts/seed.py` does it."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.bootstrap import Container, build_container
from app.guard.capability import CapabilitySigner
from app.infra.sandbox_client import SandboxClient
from app.main import create_app
from sandbox.app import create_app as create_sandbox
from sandbox.config import SandboxSettings
from scripts.seed import seed_backend, seed_guard, seed_sandbox

KEY = "s" * 48
ADMIN = "a" * 48
PIN = "7391"
WALLET = "bkash:01711000222"


@dataclass
class App:
    http: TestClient
    container: Container
    sandbox: SandboxClient

    def chat(self, message: str = "", *, forwarded: list[dict] | None = None, conversation_id: str | None = None):
        body = {"message": message, "forwarded": forwarded or []}
        if conversation_id:
            body["conversation_id"] = conversation_id
        response = self.http.post("/api/chat", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def wallet(self) -> int:
        return self.sandbox.account(WALLET)["balance_paisa"]

    def fund(self, amount_taka: int) -> None:
        """A customer pays the shop through the simulated provider (the demo shop starts with ৳7,000)."""
        response = self.http.post("/api/sim/incoming-payment", json={"from_number": "01811998877",
                                                                      "amount_taka": amount_taka})
        assert response.status_code == 200, response.text

    def approve(self, pending_id: str, pin: str | None) -> dict:
        return self.http.post(f"/api/approvals/{pending_id}/approve", json={"pin": pin}).json()

    def advance(self, hours: float) -> None:
        assert self.http.post("/api/sim/advance", json={"hours": hours}).status_code == 200

    def run_due(self) -> dict:
        return self.http.post("/api/sim/run-due").json()

    def status(self) -> dict:
        return self.http.get("/api/owner/status").json()

    def audit(self, event: str | None = None) -> list[dict]:
        return list(reversed(self.http.get("/api/audit", params={"limit": 2000, "event": event}).json()))


@pytest.fixture
def app(tmp_path, settings, clock, generated_dir):
    sandbox_http = TestClient(create_sandbox(SandboxSettings(
        _env_file=None, sandbox_data_dir=tmp_path / "sandbox", guard_signing_key=SecretStr(KEY),
        sandbox_admin_key=SecretStr(ADMIN), sim_time=clock.now())))
    sandbox_http.__enter__()
    sandbox = SandboxClient("http://testserver", admin_key=ADMIN, http=sandbox_http)
    container = build_container(settings, clock=clock, sandbox=sandbox, signer=CapabilitySigner(KEY.encode()))
    seed_backend(container.db, clock, settings.shop_id, generated_dir)
    seed_guard(container.db, clock, settings, PIN, generated_dir)
    seed_sandbox(sandbox, clock.now(), generated_dir)
    with TestClient(create_app(settings, container)) as http:
        http.headers.update({"Authorization": "Bearer test-owner-token"})
        yield App(http=http, container=container, sandbox=sandbox)
    sandbox_http.__exit__(None, None, None)
    container.close()

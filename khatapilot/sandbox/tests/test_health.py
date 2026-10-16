import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from sandbox.app import create_app
from sandbox.config import SandboxSettings


def test_sandbox_health_is_marked_simulated(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["simulated"] is True
    assert body["now"].startswith("2026-10-12T10:00")


def test_sandbox_refuses_to_start_without_secrets(tmp_path):
    settings = SandboxSettings(
        _env_file=None,
        sandbox_data_dir=tmp_path,
        guard_signing_key=SecretStr("short"),
        sandbox_admin_key=SecretStr("short"),
    )
    with pytest.raises(RuntimeError, match="make_env"):
        create_app(settings)


def test_admin_endpoints_need_the_admin_key(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/sim/reset")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ADMIN_KEY_REQUIRED"

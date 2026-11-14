"""Rate limits, security headers, request IDs and plain errors around every API request."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.interface.http_guards import HttpGuards, RateLimiter, bucket_of


def test_routes_fall_into_the_right_bucket():
    assert bucket_of("POST", "/api/approvals/pend_1/approve") == "pin"
    assert bucket_of("POST", "/api/owner/resume") == "pin"
    assert bucket_of("PUT", "/api/owner/savings-account") == "pin"
    assert bucket_of("POST", "/api/owner/payees/p1/remove") == "pin"
    assert bucket_of("POST", "/api/owner/stop") is None  # STOP is never limited
    assert bucket_of("POST", "/api/chat") == "chat" and bucket_of("POST", "/api/sim/inbound") == "chat"
    assert bucket_of("GET", "/api/savings") == "other"
    assert bucket_of("GET", "/api/stream") is None and bucket_of("GET", "/api/health") is None
    assert bucket_of("GET", "/docs") is None


def test_the_window_slides():
    now = [0.0]
    limiter = RateLimiter({"pin": 2}, clock=lambda: now[0])
    assert limiter.check("c", "pin") is None and limiter.check("c", "pin") is None
    assert limiter.check("c", "pin") == 60
    assert limiter.check("other-client", "pin") is None  # per client
    now[0] = 60.5
    assert limiter.check("c", "pin") is None


def app_with_guards(**per_minute) -> TestClient:
    app = FastAPI()

    @app.post("/api/owner/resume")
    def resume():
        return {"ok": True}

    @app.post("/api/owner/stop")
    def stop():
        return {"ok": True}

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("secret detail that must not leak")

    app.add_middleware(HttpGuards, per_minute={"pin": 0, "chat": 0, "other": 0, **per_minute})
    return TestClient(app, raise_server_exceptions=False)


def test_pin_routes_are_limited_but_stop_never_is():
    client = app_with_guards(pin=3)
    codes = [client.post("/api/owner/resume").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    limited = client.post("/api/owner/resume")
    assert int(limited.headers["retry-after"]) >= 1 and "STOP always works" in limited.json()["detail"]
    assert all(client.post("/api/owner/stop").status_code == 200 for _ in range(10))


def test_every_response_has_security_headers_and_a_request_id():
    client = app_with_guards()
    response = client.post("/api/owner/stop", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    odd = client.post("/api/owner/stop", headers={"X-Request-ID": "<script>"})
    assert odd.headers["x-request-id"].startswith("http_")  # an odd id is replaced, not echoed


def test_an_error_is_plain_and_does_not_leak(caplog):
    client = app_with_guards()
    with caplog.at_level("INFO", logger="khatapilot.http"):
        response = client.get("/api/boom")
    assert response.status_code == 500
    body = response.json()
    assert "secret detail" not in response.text and body["request_id"] == response.headers["x-request-id"]
    assert any('"status": 500' in r.getMessage() and body["request_id"] in r.getMessage() for r in caplog.records)

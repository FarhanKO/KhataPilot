"""/api/metrics: Prometheus text a monitor can scrape, with counts and states only, served to this machine
or to a scraper holding the metrics token."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.interface.metrics import route_label
from app.main import create_app


def _value(text: str, line_start: str) -> float:
    match = re.search(rf"^{re.escape(line_start)} (\S+)$", text, re.M)
    assert match, f"{line_start} not in metrics"
    return float(match.group(1))


def test_ids_in_paths_do_not_become_labels():
    assert route_label("/api/approvals/apr_7Hk2xQ9mLp/approve") == "/api/approvals/:id/approve"
    assert route_label("/api/chat/conversations/conv_Ab12Cd34") == "/api/chat/conversations/:id"
    assert route_label("/api/ledger/today") == "/api/ledger/today"
    assert route_label("/assets/index.js") == "(not api)"


def test_the_metrics_show_traffic_decisions_and_the_shops_state(app):
    app.chat("show my bills")
    app.chat("pay Fresh Dairy Point 1500")  # held for the PIN
    text = app.http.get("/api/metrics").text
    assert text.startswith("# HELP khatapilot_info")
    assert _value(text, 'khatapilot_http_requests_total{method="POST",route="/api/chat",status="200"}') == 2
    assert 'khatapilot_http_request_duration_seconds_bucket{bucket="chat",le="+Inf"} 2' in text
    assert _value(text, 'khatapilot_audit_entries_total{event="decision",decision="HELD_FOR_PIN"}') >= 1
    assert _value(text, "khatapilot_approvals_pending") == 1
    assert _value(text, "khatapilot_kill_switch_on") == 0
    assert _value(text, "khatapilot_audit_chain_ok") == 1
    assert _value(text, 'khatapilot_llm_month_turns{route="rules"}') == 2
    assert "Fresh Dairy" not in text and "1500" not in text.replace("1500.", "")  # no names or amounts

    app.http.post("/api/owner/stop", json={"reason": "test"})
    assert _value(app.http.get("/api/metrics").text, "khatapilot_kill_switch_on") == 1


def test_a_token_guards_the_metrics_when_set(app):
    app.container.settings.metrics_token = SecretStr("scrape-me")
    try:
        assert app.http.get("/api/metrics").status_code == 401  # the owner's token is not the metrics token
        ok = app.http.get("/api/metrics", headers={"Authorization": "Bearer scrape-me"})
        assert ok.status_code == 200 and "khatapilot_info" in ok.text
    finally:
        app.container.settings.metrics_token = SecretStr("")


def test_without_a_token_only_this_machine_can_read_them(app):
    remote = TestClient(create_app(app.container.settings, app.container), client=("10.1.2.3", 5000))
    assert remote.get("/api/metrics").status_code == 403


def test_the_daily_backup_runs_once_a_day_and_shows_in_the_metrics(app, tmp_path):
    from app.interface.jobs import make_backup

    settings = app.container.settings
    before = (settings.backup_daily, settings.backup_dir)
    settings.backup_daily, settings.backup_dir = True, tmp_path / "backups"
    try:
        first = make_backup(app.container)
        assert first["made"] and (tmp_path / "backups" / first["name"] / "manifest.json").exists()
        assert make_backup(app.container) == {"made": False, "why": "already made today"}
        text = app.http.get("/api/metrics").text
        assert _value(text, "khatapilot_backups_kept") == 1
        assert "khatapilot_backup_last_timestamp_seconds" in text
    finally:
        settings.backup_daily, settings.backup_dir = before

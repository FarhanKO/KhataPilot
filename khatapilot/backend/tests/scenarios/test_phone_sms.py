"""SMS from the owner's own phone (SMS Gateway for Android): booked through the guard like a forwarded SMS,
never from a personal number, never twice, and never stored when they are not about money."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.bootstrap import build_container
from app.guard.capability import CapabilitySigner
from app.infra.phone_gateway import PhoneGateway, verify_webhook
from app.infra.sandbox_client import SandboxClient
from app.main import create_app
from sandbox.app import create_app as create_sandbox
from sandbox.config import SandboxSettings
from scripts.seed import seed_backend, seed_guard, seed_sandbox
from tests.scenarios.conftest import ADMIN, KEY, PIN, App

PHONE_KEY = "phone-signing-key-for-tests"
BABUL = "01933445566"  # owes baki in the demo shop


class FakePhone:
    """The phone's Local Server API, recorded: what was sent, and an inbox to read."""

    def __init__(self, inbox: list[dict] | None = None) -> None:
        self.sent: list[dict] = []
        self.webhooks: list[dict] = []
        self.inbox = inbox or []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message":
            body = json.loads(request.content)
            self.sent.append(body)
            return httpx.Response(202, json={"id": f"m{len(self.sent)}", "state": "Pending"})
        if request.url.path == "/webhooks":
            self.webhooks.append(json.loads(request.content))
            return httpx.Response(201, json=self.webhooks[-1])
        if request.url.path == "/inbox":
            return httpx.Response(200, json=self.inbox)
        return httpx.Response(404)


@contextmanager
def phone_app(tmp_path, settings, clock, generated_dir, *, phone: FakePhone | None = None, **updates):
    live = settings.model_copy(update={"phone_webhook_key": SecretStr(PHONE_KEY), **updates})
    if phone is not None:
        live = live.model_copy(update={"phone_gateway_url": "http://phone.test"})
    sandbox_http = TestClient(create_sandbox(SandboxSettings(
        _env_file=None, sandbox_data_dir=tmp_path / "sandbox", guard_signing_key=SecretStr(KEY),
        sandbox_admin_key=SecretStr(ADMIN), sim_time=clock.now())))
    sandbox_http.__enter__()
    sandbox = SandboxClient("http://testserver", admin_key=ADMIN, http=sandbox_http)
    container = build_container(live, clock=clock, sandbox=sandbox, signer=CapabilitySigner(KEY.encode()))
    if phone is not None:
        container.phone = PhoneGateway("http://phone.test", username="u", password="p",
                                       http=httpx.Client(base_url="http://phone.test",
                                                         transport=httpx.MockTransport(phone.handler)))
    seed_backend(container.db, clock, live.shop_id, generated_dir)
    seed_guard(container.db, clock, live, PIN, generated_dir)
    seed_sandbox(sandbox, clock.now(), generated_dir)
    try:
        with TestClient(create_app(live, container)) as http:
            http.headers.update({"Authorization": "Bearer test-owner-token"})
            yield App(http=http, container=container, sandbox=sandbox)
    finally:
        sandbox_http.__exit__(None, None, None)
        container.close()


@pytest.fixture
def world(tmp_path, settings, clock, generated_dir):
    with phone_app(tmp_path, settings, clock, generated_dir) as app:
        yield app


def deliver(app: App, sender: str, message: str, *, key: str = PHONE_KEY, event: str = "sms:received"):
    """What the phone sends for one new SMS, signed the way the app signs it."""
    body = json.dumps({"deviceId": "dev1", "event": event, "id": "e1", "webhookId": "khatapilot-sms",
                       "payload": {"messageId": "m1", "message": message, "sender": sender, "recipient": None,
                                   "simNumber": 1, "receivedAt": "2026-10-12T09:58:00.000+06:00"}}).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(key.encode(), body + stamp.encode(), hashlib.sha256).hexdigest()
    return app.http.post("/api/phone/webhook", content=body,
                         headers={"X-Signature": signature, "X-Timestamp": stamp, "Content-Type": "application/json",
                                  "Authorization": ""})


def paid_in(app: App, number: str, amount_taka: int) -> dict:
    """A customer really pays the shop at the (simulated) provider; returns the SMS the phone receives."""
    response = app.http.post("/api/sim/incoming-payment", json={"from_number": number, "amount_taka": amount_taka})
    assert response.status_code == 200, response.text
    return response.json()


def unknown_trx(amount: str, number: str = "01811998877", trx: str = "ZZQ9X81KD2") -> str:
    return (f"You have received Tk {amount} from {number}. Fee Tk 0.00. Balance Tk 99,000.00. TrxID {trx} "
            "at 12/10/2026 09:58")


def phone_status(app: App) -> dict:
    return app.http.get("/api/phone").json()


def test_the_webhook_signature_is_checked():
    body = b'{"event": "sms:received"}'
    good = hmac.new(b"k", body + b"1000", hashlib.sha256).hexdigest()
    assert verify_webhook(body, good, "1000", "k", now=1000) is None
    assert verify_webhook(body, good, "1000", "other", now=1000) == "the signature does not match"
    assert verify_webhook(body, good, "1000", "k", now=1000 + 3600).startswith("the signature is too old")
    assert verify_webhook(body, None, None, "k") == "the request is not signed"


def test_an_unsigned_or_wrongly_signed_delivery_is_refused(world):
    sms = paid_in(world, "01811998877", 1200)["sms_text"]
    assert deliver(world, "bKash", sms, key="a-different-key").status_code == 401
    unsigned = world.http.post("/api/phone/webhook", json={"event": "sms:received", "payload": {}})
    assert unsigned.status_code == 401
    assert phone_status(world)["recent"] == []


def test_a_real_bkash_sms_from_the_phone_is_booked_once(world):
    payment = paid_in(world, "01811998877", 1200)
    out = deliver(world, "bKash", payment["sms_text"]).json()
    assert out["booked"] == 1
    again = deliver(world, "bKash", payment["sms_text"]).json()
    assert again["already_seen"] == 1 and "booked" not in again
    [row] = phone_status(world)["recent"]
    assert row["status"] == "booked" and row["trx_id"] == payment["trx_id"] and row["amount_paisa"] == 120_000
    entries = world.http.get("/api/ledger/entries", params={"limit": 50}).json()
    rows = entries if isinstance(entries, list) else entries.get("entries", [])
    assert sum(1 for e in rows if e.get("trx_id") == payment["trx_id"]) == 1
    execution = [e for e in world.audit("execution") if e["decision"] == "RECORDED"]
    assert execution and execution[-1]["channel"] == "phone"


def test_a_fake_bkash_sms_from_a_personal_number_is_never_booked(world):
    out = deliver(world, "+8801712345678", unknown_trx("5,000.00")).json()
    assert out == {"ok": True, "fake_sender": 1, "received": 1}
    [row] = phone_status(world)["recent"]
    assert row["status"] == "fake_sender" and row["sender"] == "017XXXX5678"
    alerts = [i for s in world.http.get("/api/notifications").json()["segments"] for i in s["items"]]
    fake = [a for a in alerts if a["title"].startswith("Possible fake bKash SMS")]
    assert fake and fake[0]["priority"] == "urgent" and "did not book it" in fake[0]["body"]


def test_sms_that_are_not_about_money_are_counted_never_stored(world):
    deliver(world, "bKash", "Your bKash verification code is 482910. Do not share it with anyone.")
    deliver(world, "01811998877", "Bhai kal dokan khola thakbe?")
    status = phone_status(world)
    assert status["recent"] == [] and status["counts"]["skipped"] == 2


def test_with_sender_id_trust_a_small_unconfirmed_sms_is_booked_and_a_large_one_waits(world):
    small = deliver(world, "bKash", unknown_trx("500.00", trx="ZZQ9X81KD2")).json()
    assert small["booked"] == 1
    large = deliver(world, "bKash", unknown_trx("45,000.00", trx="ZZQ9X81KD3")).json()
    assert large["review"] == 1
    waiting = next(r for r in phone_status(world)["recent"] if r["trx_id"] == "ZZQ9X81KD3")
    assert waiting["status"] == "review" and "waits for you" in waiting["detail"]
    booked = world.http.post(f"/api/phone/sms/{waiting['id']}/book").json()
    assert booked["status"] == "booked"
    assert world.http.post(f"/api/phone/sms/{waiting['id']}/book").status_code == 409


def test_with_provider_trust_an_unconfirmed_sms_waits_and_can_be_ignored(tmp_path, settings, clock, generated_dir):
    with phone_app(tmp_path, settings, clock, generated_dir, phone_sms_trust="provider") as app:
        assert deliver(app, "bKash", unknown_trx("500.00")).json()["review"] == 1
        [row] = phone_status(app)["recent"]
        assert app.http.post(f"/api/phone/sms/{row['id']}/ignore").json()["status"] == "ignored"
        assert phone_status(app)["recent"][0]["status"] == "ignored"


def test_money_from_a_customer_who_owes_baki_is_asked_about_first(world):
    payment = paid_in(world, BABUL, 800)
    assert deliver(world, "bKash", payment["sms_text"]).json()["asked"] == 1
    [row] = phone_status(world)["recent"]
    assert row["status"] == "asked" and row["question_id"]
    answer = world.http.post(f"/api/sms-questions/{row['question_id']}/answer", json={"purpose": "baki"})
    assert answer.status_code == 200, answer.text
    assert phone_status(world)["recent"][0]["status"] == "booked"


# ---- the same SMS forwarded in chat after the phone brought it in

def forward(app: App, text: str, sms: str) -> str:
    return app.chat(text, forwarded=[{"source": "forwarded_sms", "text": sms}])["reply"]


def entries_with(app: App, trx_id: str) -> int:
    entries = app.http.get("/api/ledger/entries", params={"limit": 200}).json()
    rows = entries if isinstance(entries, list) else entries.get("entries", [])
    return sum(1 for e in rows if e.get("trx_id") == trx_id)


def test_forwarding_an_sms_the_phone_booked_says_what_was_booked(world):
    payment = paid_in(world, "01811998877", 1200)
    assert deliver(world, "bKash", payment["sms_text"]).json()["booked"] == 1
    reply = forward(world, "record this", payment["sms_text"])
    assert reply.startswith(f"TrxID {payment['trx_id']} is already in your books"), reply
    assert "Your phone brought it in on" in reply and "৳1,200 from 018XXXX8877 as a sale" in reply
    assert "couldn't" not in reply
    assert entries_with(world, payment["trx_id"]) == 1


def test_forwarding_a_baki_payment_the_phone_booked_shows_the_baki_left(world):
    payment = paid_in(world, BABUL, 800)
    deliver(world, "bKash", payment["sms_text"])
    [row] = phone_status(world)["recent"]
    world.http.post(f"/api/sms-questions/{row['question_id']}/answer", json={"purpose": "baki"})
    reply = forward(world, "record this", payment["sms_text"])
    assert "৳800 from Babul Mia against their baki" in reply, reply
    assert "Babul Mia owes" in reply or "Babul Mia has no baki left" in reply


def test_saying_a_different_purpose_is_pointed_out_not_changed(world):
    payment = paid_in(world, BABUL, 800)
    deliver(world, "bKash", payment["sms_text"])
    [row] = phone_status(world)["recent"]
    world.http.post(f"/api/sms-questions/{row['question_id']}/answer", json={"purpose": "other"})
    reply = forward(world, "he paid back baki", payment["sms_text"])
    assert "৳800 from Babul Mia as a sale" in reply, reply
    assert "You said it was for Babul Mia's baki, but it was booked as a sale. I did not change it" in reply


def test_forwarding_an_sms_that_waits_for_an_answer_books_it_and_closes_the_wait(world):
    payment = paid_in(world, BABUL, 800)
    assert deliver(world, "bKash", payment["sms_text"]).json()["asked"] == 1
    reply = forward(world, "he paid back baki", payment["sms_text"])
    assert "Booked the bKash SMS" in reply and "it no longer waits" in reply, reply
    [row] = phone_status(world)["recent"]
    assert row["status"] == "booked"
    answer = world.http.post(f"/api/sms-questions/{row['question_id']}/answer", json={"purpose": "baki"})
    assert answer.status_code != 200, "the question was closed when the chat booked it"
    assert entries_with(world, payment["trx_id"]) == 1


def test_forwarding_an_sms_that_waits_for_review_points_to_it(world):
    sms = unknown_trx("45,000.00", trx="ZZQ9X81KD3")
    assert deliver(world, "bKash", sms).json()["review"] == 1
    reply = forward(world, "record this", sms)
    assert "Booked" not in reply and "waiting for you in the Ledger tab under Phone SMS" in reply, reply
    assert phone_status(world)["recent"][0]["status"] == "review"


def test_forwarding_a_fake_the_phone_caught_warns_again(world):
    sms = unknown_trx("5,000.00")
    deliver(world, "+8801712345678", sms)
    reply = forward(world, "record this", sms)
    assert "Booked" not in reply and "017XXXX5678" in reply and "looks like a fake" in reply, reply


def test_a_sender_that_does_not_match_the_text_waits_for_the_owner(world):
    payment = paid_in(world, "01811998877", 700)
    assert deliver(world, "NAGAD", payment["sms_text"]).json()["review"] == 1


def test_a_backup_file_and_pasted_text_are_imported(world):
    first = paid_in(world, "01811998877", 300)
    xml = ("<?xml version='1.0' encoding='UTF-8'?><smses count='3'>"
           f"<sms address='bKash' date='1760241480000' type='1' body='{first['sms_text']}' />"
           "<sms address='01811998877' date='1760241480000' type='1' body='Bhai taka pathaisi' />"
           f"<sms address='bKash' date='1760241480000' type='2' body='{unknown_trx('9.00')}' />"
           "</smses>")
    out = world.http.post("/api/phone/import", json={"xml": xml}).json()
    assert out["booked"] == 1 and out["skipped"] == 1 and out["received"] == 2
    second = paid_in(world, "01811998877", 400)
    pasted = world.http.post("/api/phone/import", json={"text": second["sms_text"] + "\n\n" + unknown_trx("60.00")})
    result = pasted.json()
    assert result["booked"] == 1, result  # confirmed by the provider
    assert result["not_booked"] == 1, result  # no sender: only the provider's word counts, as in chat


def test_pulling_reads_the_phone_and_registers_the_webhook(tmp_path, settings, clock, generated_dir):
    phone = FakePhone()
    with phone_app(tmp_path, settings, clock, generated_dir, phone=phone) as app:
        payment = paid_in(app, "01811998877", 950)
        phone.inbox = [{"id": "1", "type": "SMS", "sender": "bKash", "contentPreview": payment["sms_text"],
                        "createdAt": "2026-10-12T09:58:00+06:00"},
                       {"id": "2", "type": "SMS", "sender": "01711000000", "contentPreview": "Kemon acho?",
                        "createdAt": "2026-10-12T09:59:00+06:00"}]
        out = app.http.post("/api/phone/pull").json()
        assert out["booked"] == 1 and out["skipped"] == 1
        assert phone.webhooks == [{"id": "khatapilot-sms", "url": settings.phone_webhook_url, "event": "sms:received"}]
        assert app.http.post("/api/phone/pull").json().get("already_seen") == 1


def test_approved_reminders_also_leave_from_the_owners_phone(tmp_path, settings, clock, generated_dir):
    phone = FakePhone()
    with phone_app(tmp_path, settings, clock, generated_dir, phone=phone, sms_via_phone=True) as app:
        held = app.http.post("/api/reminders/prepare", json={"min_days": 14}).json()
        assert held["status"] == "held", held
        result = app.approve(held["pending_id"], None)
        assert result["ok"], result
        assert phone.sent and all(m["phoneNumbers"][0].startswith("+8801") for m in phone.sent)
        bodies = {m["textMessage"]["text"] for m in phone.sent}
        assert bodies == {m["body"] for m in app.sandbox.messages()}, "the phone sends exactly what the guard signed"


def test_urgent_alerts_are_texted_to_the_owner_once(tmp_path, settings, clock, generated_dir):
    from app.interface.jobs import send_alert_sms

    phone = FakePhone()
    with phone_app(tmp_path, settings, clock, generated_dir, phone=phone, sms_via_phone=True, owner_alert_sms=True,
                   owner_phone="01700000001", owner_alert_sms_per_day=1) as app:
        assert send_alert_sms(app.container) == {"sent": 0}
        deliver(app, "01712345678", unknown_trx("5,000.00"))
        out = send_alert_sms(app.container)
        assert out["sent"] == 1 and out["alerts"] == 1
        [text] = phone.sent
        assert text["phoneNumbers"] == ["+8801700000001"]
        assert text["textMessage"]["text"].startswith("KhataPilot: Possible fake bKash SMS from 017XXXX5678")
        assert [m["to_masked"] for m in app.sandbox.messages()] == ["017XXXX0001"], "the gateway checked the token"
        assert send_alert_sms(app.container) == {"sent": 0}, "each alert is texted once"
        deliver(app, "01712345679", unknown_trx("7,000.00", trx="ZZQ9X81KD9"))
        assert send_alert_sms(app.container)["why"] == "today's alert SMS are used up"
        assert len(phone.sent) == 1
        sent = [e for e in app.audit("execution") if e["summary"].startswith("Alert SMS to the owner")]
        assert [e["decision"] for e in sent] == ["SENT"]


def test_a_customers_stop_reply_by_sms_is_recorded_without_the_model(world):
    assert deliver(world, "+8801933445566", "STOP").json()["stopped"] == 1
    opted = world.http.get("/api/reminders").json()["opted_out"]
    assert [o["source"] for o in opted] == ["customer"] and opted[0]["number_masked"] == "019XXXX5566"
    assert deliver(world, "01933445566", "start").json()["started"] == 1
    assert world.http.get("/api/reminders").json()["opted_out"] == []
    assert phone_status(world)["recent"] == [], "replies are not kept as SMS"

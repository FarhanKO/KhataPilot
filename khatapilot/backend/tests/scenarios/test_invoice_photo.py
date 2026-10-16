"""An invoice photo, end to end: the owner photographs it, checks the text, and forwards it."""

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(app, name: str) -> str:
    photo = base64.b64encode((ROOT / "frontend/public/samples" / name).read_bytes()).decode()
    response = app.http.post("/api/chat/photo", json={"image_base64": photo})
    assert response.status_code == 200, response.text
    return response.json()["text"]


def test_reading_a_photo_changes_nothing(app):
    before = app.audit()
    read(app, "invoice-shapla.png")
    assert app.audit() == before, "reading a photo records, pays and tells the agent nothing"


def test_a_doctored_invoice_photo_is_flagged_and_its_payment_blocked(app):
    wallet = app.wallet()
    reply = app.chat("here is the packaging invoice",
                     forwarded=[{"source": "invoice_text", "text": read(app, "invoice-shapla.png")}])
    assert reply["quarantine"][0]["kind"] == "invoice" and reply["quarantine"][0]["suspected"]
    blocked = [o for o in reply["outcomes"] if o["tool"] == "propose_payment"]
    assert blocked and all(o["status"] == "blocked" for o in blocked)
    assert app.wallet() == wallet


def test_a_clean_invoice_photo_reads_as_an_invoice(app):
    reply = app.chat("here is Meghna's invoice",
                     forwarded=[{"source": "invoice_text", "text": read(app, "invoice-meghna.png")}])
    report = reply["quarantine"][0]
    assert report["kind"] == "invoice" and not report["suspected"]
    assert "Meghna Distributors" in report["summary"] and "42,175" in report["summary"]


def test_a_days_invoice_photos_go_in_one_message(app):
    """The owner reads a whole day's photos at closing time and forwards them together (up to 20)."""
    meghna, shapla = read(app, "invoice-meghna.png"), read(app, "invoice-shapla.png")
    batch = [{"source": "invoice_text", "text": text} for text in [meghna, shapla] * 10]
    reply = app.chat("today's invoices", forwarded=batch)
    assert len(reply["quarantine"]) == 20
    assert sum(q["suspected"] for q in reply["quarantine"]) == 10, "every doctored copy is still flagged"
    too_many = app.http.post("/api/chat", json={"message": "", "forwarded": batch + batch[:1]})
    assert too_many.status_code == 422

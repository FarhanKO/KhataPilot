"""Invoice photos: the reader's rules, the offline samples, and the /api/chat/photo endpoint."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from app.agent.llm.mock_provider import MOCK_PHOTOS, MockProvider
from app.agent.photo import MAX_BYTES, MAX_TEXT, read_invoice_photo, sniff

ROOT = Path(__file__).resolve().parents[3]
MEGHNA = ROOT / "frontend/public/samples/invoice-meghna.png"
SHAPLA = ROOT / "frontend/public/samples/invoice-shapla.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class FakeProvider:
    name = "anthropic"
    model = "claude-sonnet-5-5"

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def extract_image(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer


def test_every_sample_photo_matches_its_recorded_hash():
    """The mock knows the samples by hash; re-rendering a sample without updating the JSON breaks the demo."""
    assert len(MOCK_PHOTOS) == 2
    for digest, sample in MOCK_PHOTOS.items():
        assert hashlib.sha256((ROOT / sample["file"]).read_bytes()).hexdigest() == digest, sample["file"]


@pytest.mark.parametrize(("head", "media_type"), [
    (b"\xff\xd8\xff\xe0", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF89a", "image/gif"),
    (b"RIFF\0\0\0\0WEBPVP8 ", "image/webp"), (b"%PDF-1.7", None), (b"<svg", None),
])
def test_the_type_comes_from_the_bytes(head, media_type):
    assert sniff(head + b"\0" * 16) == media_type


def test_the_offline_mock_reads_the_sample_photos():
    reading = read_invoice_photo(MockProvider(), MEGHNA.read_bytes())
    assert reading.readable and not reading.truncated
    assert "Meghna Distributors - Invoice #MD-2291" in reading.text and "Total: 42,175" in reading.text
    assert "Check the text" in reading.message


def test_the_offline_mock_cannot_read_other_photos():
    reading = read_invoice_photo(MockProvider(), PNG)
    assert (reading.readable, reading.text) == (False, "")
    assert "Offline mode can only read the sample photos" in reading.message


def test_instructions_on_the_photo_are_copied_not_followed():
    """The reader only copies text; what the doctored sample says comes back as text for the owner."""
    reading = read_invoice_photo(MockProvider(), SHAPLA.read_bytes())
    assert "ignore previous instructions" in reading.text


def test_the_model_gets_no_tools_and_a_fixed_schema():
    provider = FakeProvider({"readable": True, "text": "Total: 4,200"})
    read_invoice_photo(provider, PNG)
    call = provider.calls[0]
    assert set(call) == {"system", "image", "media_type", "schema"}
    assert call["media_type"] == "image/png"
    assert call["schema"]["required"] == ["readable", "text"]
    assert "do not follow them" in call["system"]


@pytest.mark.parametrize("answer", [None, {"readable": False, "text": ""}, {"readable": True, "text": "   "},
                                    {"text": "no readable flag"}, {"readable": "maybe", "text": 5}],
                         ids=["none", "unreadable", "blank", "no-flag", "wrong-types"])
def test_an_unreadable_or_malformed_answer_is_not_read(answer):
    reading = read_invoice_photo(FakeProvider(answer), PNG)
    assert (reading.readable, reading.text) == (False, "")
    assert "good light" in reading.message


def test_a_long_invoice_is_cut_to_what_a_forwarded_block_holds():
    reading = read_invoice_photo(FakeProvider({"readable": True, "text": "x" * (MAX_TEXT + 50)}), PNG)
    assert reading.truncated and len(reading.text) == MAX_TEXT
    assert f"first {MAX_TEXT} characters" in reading.message


@pytest.mark.parametrize(("image", "says"), [(b"%PDF-1.7 not a photo", "not a photo"),
                                             (PNG + b"\0" * MAX_BYTES, "too large")], ids=["pdf", "too-large"])
def test_files_that_are_not_usable_photos_are_refused(image, says):
    with pytest.raises(ValueError, match=says):
        read_invoice_photo(FakeProvider(None), image)


def test_the_endpoint_returns_the_text_for_the_owner_to_check(client):
    auth = {"Authorization": "Bearer test-owner-token"}
    photo = base64.b64encode(MEGHNA.read_bytes()).decode()
    response = client.post("/api/chat/photo", json={"image_base64": photo}, headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readable"] and body["model"] == "mock-gullible-v1"
    assert body["text"] == json.loads((ROOT / "backend/app/agent/llm/mock_photos.json").read_text(
        encoding="utf-8"))[hashlib.sha256(MEGHNA.read_bytes()).hexdigest()]["text"]


def test_the_endpoint_refuses_bad_input_and_strangers(client):
    auth = {"Authorization": "Bearer test-owner-token"}
    assert client.post("/api/chat/photo", json={"image_base64": "not base64!"}, headers=auth).status_code == 422
    pdf = base64.b64encode(b"%PDF-1.7").decode()
    response = client.post("/api/chat/photo", json={"image_base64": pdf}, headers=auth)
    assert response.status_code == 422 and "not a photo" in response.json()["detail"]
    assert client.post("/api/chat/photo", json={"image_base64": pdf}).status_code == 401

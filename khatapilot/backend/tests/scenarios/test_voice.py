"""Voice input: a recording becomes text on this machine, steered toward the shop's own names and tidied;
it only fills the message box. Uses a stand-in for the Whisper model (the real one is 1.6 GB)."""

from __future__ import annotations

import pytest

from app.infra.speech import Heard
from app.interface.voice_text import prompt_for, tidy


class FakeSpeech:
    model_name = "fake-whisper"

    def __init__(self, heard: str) -> None:
        self.heard = heard
        self.calls: list[dict] = []

    def available(self) -> str | None:
        return None

    def transcribe(self, audio: bytes, *, language, prompt: str, hotwords: str) -> Heard:
        self.calls.append({"audio": audio, "language": language, "prompt": prompt, "hotwords": hotwords})
        return Heard(text=self.heard, language=language or "en", language_probability=1.0, seconds=3.2, took=0.4)


def test_without_a_model_the_browser_reads_the_voice(app):
    assert app.http.get("/api/voice").json() == {"server": False, "model": None,
                                                 "why": "voice transcription is turned off (STT_ENABLED)"}
    refused = app.http.post("/api/voice/transcribe", content=b"\x1aE\xdf\xa3 webm",
                            headers={"Content-Type": "audio/webm"})
    assert refused.status_code == 503 and "browser instead" in refused.json()["detail"]


def test_a_recording_becomes_tidy_text_with_the_shops_own_names(app):
    fake = FakeSpeech("send backy reminder to Jahangir Allam and babul mia, pay rahim traders five thousand "
                      "taka by bikash")
    app.container.speech = fake
    assert app.http.get("/api/voice").json()["server"] is True
    out = app.http.post("/api/voice/transcribe?lang=en", content=b"\x1aE\xdf\xa3 webm",
                        headers={"Content-Type": "audio/webm"}).json()
    assert out["text"] == ("send baki reminder to Jahangir Alam and Babul Mia, pay Rahim Traders 5000 taka by "
                           "bKash")
    assert out["raw"].startswith("send backy") and out["model"] == "fake-whisper"
    [call] = fake.calls
    assert call["language"] == "en" and call["audio"] == b"\x1aE\xdf\xa3 webm"
    assert "Jahangir Alam" in call["prompt"] and "Rahim Traders" in call["prompt"] and "baki" in call["prompt"]


def test_bangla_gets_a_bangla_prompt_and_auto_detects_when_asked(app):
    fake = FakeSpeech("রহিমকে ৫০০ টাকা দাও")
    app.container.speech = fake
    app.http.post("/api/voice/transcribe?lang=bn", content=b"audio")
    app.http.post("/api/voice/transcribe?lang=auto", content=b"audio")
    assert [c["language"] for c in fake.calls] == ["bn", None]
    assert "বাকি" in fake.calls[0]["prompt"]


def test_voice_needs_the_owner(app):
    app.container.speech = FakeSpeech("pay Rahim")
    response = app.http.post("/api/voice/transcribe", content=b"audio", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


@pytest.mark.parametrize(("heard", "tidied"), [
    ("pay rahim traders five thousand five hundred taka by bikash", "pay Rahim Traders 5500 taka by bKash"),
    ("sold rice one thousand two hundred and fifty taka cash", "sold rice 1250 taka cash"),
    ("two lakh taka nogod e pathao", "200000 taka Nagad e pathao"),
    ("Rahim ke 5, 000 taka dao", "Rahim ke 5000 taka dao"),
    ("I have one customer who owes", "I have one customer who owes"),  # a lone small number stays a word
    ("Babul bhai er backy koto", "Babul bhai er baki koto"),
])
def test_tidying(heard, tidied):
    assert tidy(heard, ["Rahim Traders", "Babul Mia", "Jahangir Alam"]) == tidied


def test_a_name_is_snapped_only_when_it_is_very_close():
    assert tidy("remind Sumi Aktar", ["Sumi Akter"]) == "remind Sumi Akter"
    assert tidy("remind Shirin", ["Sumi Akter", "Shirin Akter"]) == "remind Shirin"


def test_the_prompt_lists_names_first_and_is_bounded():
    names = [f"Customer {i}" for i in range(100)]
    prompt, hotwords = prompt_for(names, bangla=False)
    assert prompt.startswith("Customer 0, Customer 1") and "Customer 40" not in prompt and "bKash" in hotwords

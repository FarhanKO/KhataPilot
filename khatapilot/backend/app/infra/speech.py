"""Offline speech-to-text with faster-whisper (Whisper on CTranslate2), loaded once and kept in memory.

Optional: without `faster-whisper` installed or without the downloaded model, `available()` says why and the
app falls back to the browser's own recognizer. Nothing leaves the machine; the model runs on the CPU.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Heard:
    text: str
    language: str
    language_probability: float
    seconds: float  # length of the speech
    took: float  # how long transcribing took


MAX_SECONDS = 60


def decode(audio: bytes):
    """Any recording a browser makes (webm/opus, ogg, mp4/aac, wav) as 16 kHz mono float32 samples."""
    import av
    import numpy as np

    chunks = []
    with av.open(io.BytesIO(audio), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("There is no sound in that recording.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16_000)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise ValueError("There is no sound in that recording.")
    samples = np.concatenate(chunks)[: MAX_SECONDS * 16_000]
    return samples.astype(np.float32) / 32768.0


class SpeechToText:
    def __init__(self, *, model: str, model_dir: Path, threads: int = 0) -> None:
        self.model_name = model
        self.model_dir = model_dir
        self.threads = threads
        self._model = None
        self._lock = threading.Lock()
        self._problem: str | None = None

    def _path(self) -> Path | None:
        """The downloaded model folder (scripts/get_voice_model.py), or None."""
        root = self.model_dir / self.model_name
        found = [p.parent for p in root.rglob("model.bin")] if root.exists() else []
        return found[0] if found else None

    def available(self) -> str | None:
        """None when voice can be transcribed here, else why not."""
        if self._model is not None:
            return None
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return "faster-whisper is not installed (pip install -r requirements-voice.txt)"
        if self._path() is None:
            return f"the {self.model_name} model is not downloaded (python scripts/get_voice_model.py)"
        return self._problem

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    started = time.perf_counter()
                    try:
                        self._model = WhisperModel(str(self._path()), device="cpu", compute_type="int8",
                                                   cpu_threads=self.threads)
                    except Exception as exc:  # a broken download: say so instead of failing every time
                        self._problem = f"the speech model could not be loaded ({exc})"
                        raise
                    log.info("speech model %s loaded in %.1fs", self.model_name, time.perf_counter() - started)
        return self._model

    def warm_up(self) -> None:
        """Load the model in the background at startup, so the first tap on the mic is not slow."""
        if self.available() is None:
            threading.Thread(target=self._safe_load, name="stt-warmup", daemon=True).start()

    def _safe_load(self) -> None:
        try:
            self._load()
        except Exception:  # noqa: BLE001
            log.exception("speech model warm-up failed")

    def transcribe(self, audio: bytes, *, language: str | None, prompt: str, hotwords: str) -> Heard:
        model = self._load()
        started = time.perf_counter()
        segments, info = model.transcribe(
            decode(audio), language=language, initial_prompt=prompt or None, hotwords=hotwords or None,
            beam_size=5, temperature=0.0, condition_on_previous_text=False, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500})
        text = " ".join(s.text.strip() for s in segments).strip()
        return Heard(text=text, language=info.language, language_probability=round(info.language_probability, 3),
                     seconds=round(info.duration_after_vad or info.duration, 2),
                     took=round(time.perf_counter() - started, 2))

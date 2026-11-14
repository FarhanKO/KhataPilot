"""Download the offline speech-to-text model once, so voice input works with no internet (the venue).

    python scripts/get_voice_model.py                 # STT_MODEL from .env (default large-v3-turbo)
    python scripts/get_voice_model.py --model small   # a smaller, faster one for slow laptops

It goes to STT_MODEL_DIR (default var/models/whisper, gitignored). Needs `pip install -r requirements-voice.txt`.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.core.config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=settings.stt_model)
    args = parser.parse_args()
    try:
        from faster_whisper import download_model
    except ImportError:
        print("faster-whisper is not installed: pip install -r requirements-voice.txt")
        return 1
    target = settings.stt_model_dir / args.model
    started = time.perf_counter()
    path = download_model(args.model, output_dir=str(target))
    size = sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) / 1e6
    print(f"{args.model}: {size:,.0f} MB in {path} ({time.perf_counter() - started:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

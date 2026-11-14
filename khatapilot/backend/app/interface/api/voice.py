"""Voice input: the owner's recording is turned into text on this machine (an offline Whisper model), steered
toward the shop's own customer and supplier names and Banglish words, tidied, and handed back. The text only
goes into the message box: nothing is sent, booked or paid until the owner reads it and presses send."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.core.provenance import Sender
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner
from app.interface.voice_text import prompt_for, tidy

router = APIRouter(prefix="/voice", tags=["voice"])
MAX_AUDIO_BYTES = 8_000_000  # about a minute of browser audio, with room to spare


def _names(container) -> list[str]:
    """The names most likely to be said: who owes baki (longest owing first), suppliers, trusted payees."""
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        shop = container.settings.shop_id
        owing = sorted(guard.ledger.baki_by_customer(shop), key=lambda b: -(b.days_outstanding or 0))
        names = [b.name for b in owing] + [p.name for p in guard.shops.parties(shop, kind="supplier")]
        names += [p.name for p in guard.store.payees()]
    seen, ordered = set(), []
    for name in names:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            ordered.append(name)
    return ordered


@router.get("")
def voice_status(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    stt = container.speech
    why = "voice transcription is turned off (STT_ENABLED)" if stt is None else stt.available()
    return {"server": why is None, "model": stt.model_name if stt else None, "why": why}


@router.post("/transcribe")
async def transcribe(request: Request, lang: Literal["en", "bn", "auto"] = Query(default="en"),
                     container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    stt = container.speech
    why = "voice transcription is turned off" if stt is None else stt.available()
    if why:
        raise HTTPException(503, f"Voice is read by the browser instead: {why}.")
    audio = await request.body()
    if not audio:
        raise HTTPException(422, "No recording arrived.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long; please keep it under a minute.")
    names = await run_in_threadpool(_names, container)
    prompt, hotwords = prompt_for(names, bangla=lang == "bn")
    try:
        heard = await run_in_threadpool(stt.transcribe, audio, language=None if lang == "auto" else lang,
                                        prompt=prompt, hotwords=hotwords)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:  # a recording the decoder cannot read
        raise HTTPException(422, f"I could not hear that recording ({exc.__class__.__name__}). Please try again.") \
            from None
    text = tidy(heard.text, names)
    if container.metrics:
        container.metrics.voice_transcribed(heard.took)
    return {"text": text, "raw": heard.text, "language": heard.language, "seconds": heard.seconds,
            "took": heard.took, "model": stt.model_name}

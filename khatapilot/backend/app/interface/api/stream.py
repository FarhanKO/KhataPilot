"""The live decision trace as server-sent events: recent history first, then every new event."""

from __future__ import annotations

import asyncio
import json
import queue

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.provenance import Sender
from app.interface.deps import get_container, require_owner

router = APIRouter(tags=["trace"])
KEEPALIVE_SECONDS = 15.0
POLL_SECONDS = 0.15


def _sse(event: dict) -> str:
    return f"id: {event.get('trace_id', '')}\ndata: {json.dumps(event, default=str, ensure_ascii=False)}\n\n"


@router.get("/trace/stream")
async def trace_stream(request: Request, replay: int = Query(default=100, ge=0, le=300), once: bool = False,
                       container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> StreamingResponse:
    """`once=true` sends the history and closes (for tests and for clients that only want a snapshot)."""
    bus = container.trace

    async def events():
        with bus.subscribe() as inbox:  # subscribe first, so nothing published during the replay is lost
            for event in bus.recent(replay):
                yield _sse(event)
            if once:
                return
            yield ": live\n\n"
            idle = 0.0
            while not await request.is_disconnected():
                try:
                    event = inbox.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(POLL_SECONDS)
                    idle += POLL_SECONDS
                    if idle >= KEEPALIVE_SECONDS:
                        idle = 0.0
                        yield ": keepalive\n\n"
                    continue
                idle = 0.0
                yield _sse(event)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

"""Composition root: builds the FastAPI app and wires the layers together.

Run with:  python -m uvicorn app.main:create_app --factory --app-dir backend --port 8000
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.bootstrap import Container, build_container
from app.core.config import Settings, get_settings
from app.interface.api import approvals, chat, credit, health, owner, reminders, stream, views
from app.interface.jobs import scheduler_loop


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = app.state.container is None
        if owned:
            app.state.container = build_container(settings)
        scheduler = None
        if settings.scheduler_interval_seconds > 0:
            scheduler = asyncio.create_task(scheduler_loop(app.state.container, settings.scheduler_interval_seconds))
        yield
        if scheduler:
            scheduler.cancel()
        if owned:
            app.state.container.close()

    app = FastAPI(
        title="KhataPilot",
        version=__version__,
        description="An AI CFO for micro-shops. The LLM proposes; a deterministic guard decides.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (health, chat, approvals, owner, views, credit, reminders, stream):
        app.include_router(module.router, prefix="/api")
    return app

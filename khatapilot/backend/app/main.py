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
from app.infra import logs
from app.interface.api import (
    approvals,
    chat,
    credit,
    growth,
    health,
    notifications,
    owner,
    phone,
    reminders,
    reports,
    savings,
    sms_questions,
    stream,
    views,
    voice,
)
from app.interface.http_guards import HttpGuards
from app.interface import metrics
from app.interface.jobs import scheduler_loop


def _count_error(app: FastAPI) -> None:
    metrics = getattr(getattr(app.state, "container", None), "metrics", None)
    if metrics is not None:
        metrics.error()


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.app_env != "test":
        logs.configure(log_dir=settings.data_dir / "logs", fmt=settings.log_format, level=settings.log_level,
                       sentry_dsn=settings.sentry_dsn, on_error=lambda: _count_error(app))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = app.state.container is None
        if owned:
            app.state.container = build_container(settings)
        if app.state.container.speech is not None and settings.app_env != "test":
            app.state.container.speech.warm_up()  # load the voice model now, not on the first tap of the mic
        scheduler = None
        if settings.scheduler_interval_seconds > 0 and settings.scheduler_in_web:
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
    # Outermost of ours, so CORS preflights and every response get the request ID and headers too.
    app.add_middleware(HttpGuards, reraise=settings.app_env == "test", per_minute={
        "pin": settings.rate_limit_pin_per_minute, "chat": settings.rate_limit_chat_per_minute,
        "other": settings.rate_limit_other_per_minute})
    for module in (health, chat, approvals, owner, views, credit, reminders, sms_questions, stream, notifications,
                   savings, growth, reports, phone, voice, metrics):
        app.include_router(module.router, prefix="/api")
    return app

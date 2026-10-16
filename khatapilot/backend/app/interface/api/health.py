from fastapi import APIRouter, Request

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    settings = request.app.state.settings
    container = request.app.state.container
    return {
        "status": "ok",
        "service": "khatapilot-backend",
        "version": __version__,
        "llm_mode": settings.llm_mode,
        "risk_model": container.risk_model_name if container else None,
        "sandbox_url": settings.sandbox_url,
    }

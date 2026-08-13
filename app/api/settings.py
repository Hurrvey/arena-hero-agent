"""Non-sensitive, read-only local console settings."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1")


@router.get("/settings")
def settings(request: Request) -> dict[str, object]:
    provider = os.environ.get("ARENA_HERO_LLM_BASE_URL", "https://api.openai.com/v1")
    hostname = urlsplit(provider).hostname or "未配置"
    return {
        "rawRetentionDays": 7,
        "eventRetentionDays": 30,
        "logLevel": "INFO",
        "providerConfigured": bool(os.environ.get("ARENA_HERO_LLM_API_KEY")),
        "providerHost": hostname,
        "model": os.environ.get("ARENA_HERO_LLM_MODEL", "未配置"),
        "mapRefresh": "LIVE_EVENT_DRIVEN",
        "databaseLocation": request.app.state.services.settings.database_path.name,
    }

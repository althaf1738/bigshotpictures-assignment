from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import get_settings
from app.observability.logging import get_logger

logger = get_logger(__name__)


async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    settings = get_settings()
    if x_api_key not in settings.api_keys:
        logger.warning("invalid_api_key", key_prefix=x_api_key[:8] if len(x_api_key) >= 8 else "***")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return x_api_key

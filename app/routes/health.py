from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.observability.metrics import get_metrics

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@router.get("/metrics")
async def metrics(request: Request, db: AsyncSession = Depends(get_db)):
    uptime_seconds = int(time.monotonic() - request.app.state.started_at)
    return await get_metrics(db, uptime_seconds)

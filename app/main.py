from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.analysis.strategy import create_strategy
from app.config import get_settings
from app.db.migrations import run_migrations
from app.db.session import create_tables, engine
from app.observability.logging import configure_logging, correlation_id_var, get_logger
from app.routes.dashboard import router as dashboard_router
from app.routes.health import router as health_router
from app.routes.submissions import router as submissions_router
from app.routing.router import TierRouter

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    await create_tables()
    if engine.dialect.name == "sqlite":
        await run_migrations(engine)
    else:
        logger.info("manual_migrations_skipped", dialect=engine.dialect.name)
    app.state.strategy = create_strategy(settings)
    app.state.tier_router = TierRouter()
    logger.info("startup_complete", fast_pool=settings.fast_pool, rich_pool=settings.rich_pool)
    yield
    logger.info("shutdown")


settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.rate_limit_per_minute}/minute"])

app = FastAPI(
    title="Creative Review API",
    version="2.0.0",
    description="AI-powered analysis of creative submissions",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.state.started_at = time.monotonic()
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next) -> Response:
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    token = correlation_id_var.set(correlation_id)
    start = time.monotonic()
    try:
        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "request_complete",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
            client_ip=request.client.host if request.client else None,
            correlation_id=correlation_id,
        )
        return response
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            "request_failed",
            method=request.method,
            path=request.url.path,
            latency_ms=latency_ms,
            client_ip=request.client.host if request.client else None,
            error_type=type(exc).__name__,
            error=str(exc),
            correlation_id=correlation_id,
        )
        raise
    finally:
        correlation_id_var.reset(token)


app.include_router(health_router)
app.include_router(submissions_router)
app.include_router(dashboard_router)


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/dashboard")

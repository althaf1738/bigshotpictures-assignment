from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.routing.router import TierRouter
from app.schemas.analysis import AnalysisResult, CreativeScore

TEST_API_KEY = "testkey"
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

_MOCK_RESULT = AnalysisResult(
    summary="Mock summary",
    scores=[CreativeScore(dimension="concept", score=8, rationale="Good")],
    strengths=["Strong"],
    improvements=["Improve"],
    target_audience="Everyone",
    tone="Neutral",
    recommendations=["Do this"],
    confidence=0.9,
    tier_used="fast",
    model_used="mock",
)


class MockStrategy:
    """Deterministic no-network strategy for tests."""

    async def run_fast(self, brief: str, images, correlation_id: str):
        from app.analysis.strategy import StrategyResult
        return StrategyResult(
            result=AnalysisResult(**{**_MOCK_RESULT.model_dump(), "tier_used": "fast"}),
            requested_tier="fast",
            tier_used="fast",
            stages_completed=["single"],
            pool_attempts=[],
            total_latency_ms=10,
            degraded=False,
            degradation_reason=None,
            model_used="mock",
        )

    async def run_rich(self, brief: str, images, correlation_id: str):
        from app.analysis.strategy import StrategyResult
        return StrategyResult(
            result=AnalysisResult(**{**_MOCK_RESULT.model_dump(), "tier_used": "rich"}),
            requested_tier="rich",
            tier_used="rich",
            stages_completed=["stage_1", "stage_2"],
            pool_attempts=[],
            total_latency_ms=50,
            degraded=False,
            degradation_reason=None,
            model_used="mock",
        )


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def tier_router():
    return TierRouter()


@pytest.fixture
async def async_client(db_engine):
    import app.tasks.analyze as analyze_module

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    original_session_local = analyze_module.AsyncSessionLocal

    analyze_module.AsyncSessionLocal = factory

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.state.strategy = MockStrategy()
    app.state.tier_router = TierRouter()

    settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
    existing = settings.api_keys_raw
    if TEST_API_KEY not in existing:
        settings.api_keys_raw = f"{existing},{TEST_API_KEY}" if existing else TEST_API_KEY

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        yield client

    app.dependency_overrides.clear()
    analyze_module.AsyncSessionLocal = original_session_local

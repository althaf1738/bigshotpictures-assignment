from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis
from app.schemas.analysis import AnalysisResult


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        submission_id: str,
        tier_used: str,
        model_used: str,
        result: AnalysisResult,
        total_latency_ms: int = 0,
        degraded: bool = False,
        degradation_reason: str | None = None,
        processing_metadata: dict | None = None,
        prompt_version: str = "1.0",
    ) -> Analysis:
        analysis = Analysis(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            tier_used=tier_used,
            model_used=model_used,
            result=result.model_dump(),
            total_latency_ms=total_latency_ms,
            degraded=degraded,
            degradation_reason=degradation_reason,
            processing_metadata=processing_metadata,
            prompt_version=prompt_version,
        )
        self._session.add(analysis)
        await self._session.commit()
        await self._session.refresh(analysis)
        return analysis

    async def get_by_submission(self, submission_id: str) -> list[Analysis]:
        result = await self._session.execute(
            select(Analysis)
            .where(Analysis.submission_id == submission_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        )
        return list(result.scalars().all())

    async def get_latest_by_submission(self, submission_id: str) -> Analysis | None:
        result = await self._session.execute(
            select(Analysis)
            .where(Analysis.submission_id == submission_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Submission
from app.observability.logging import correlation_id_var, get_logger
from app.schemas.submission import SubmissionCreate

logger = get_logger(__name__)


class SubmissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: SubmissionCreate) -> Submission:
        submission = Submission(
            id=str(uuid.uuid4()),
            title=data.title,
            brief_text=data.brief_text,
            asset_urls=data.asset_urls,
            tier_requested=data.tier_override,
            content_type=data.content_type,
            status="pending",
        )
        self._session.add(submission)
        await self._session.commit()
        await self._session.refresh(submission)
        return submission

    async def get(self, submission_id: str) -> Submission | None:
        result = await self._session.execute(
            select(Submission)
            .where(Submission.id == submission_id)
            .options(selectinload(Submission.analyses))
        )
        return result.scalar_one_or_none()

    async def list(self, limit: int = 20, offset: int = 0) -> list[Submission]:
        result = await self._session.execute(
            select(Submission)
            .options(selectinload(Submission.analyses))
            .order_by(Submission.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update_status(self, submission_id: str, status: str) -> None:
        submission = await self._session.get(Submission, submission_id)
        if submission:
            previous_status = submission.status
            submission.status = status
            await self._session.commit()
            logger.info(
                "analysis_status_changed",
                submission_id=submission_id,
                from_status=previous_status,
                to_status=status,
                correlation_id=correlation_id_var.get(),
            )

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.session import get_db
from app.observability.logging import correlation_id_var, get_logger
from app.repositories.analyses import AnalysisRepository
from app.repositories.submissions import SubmissionRepository
from app.routing.router import TierRouter
from app.schemas.analysis import AnalysisResult
from app.schemas.submission import (
    AnalysisDetailResponse,
    AuditResponse,
    SubmissionCreate,
    SubmissionDetailResponse,
    SubmissionSummaryResponse,
)
from app.tasks.analyze import run_analysis

logger = get_logger(__name__)
router = APIRouter(prefix="/submissions", tags=["submissions"])


def _to_detail(submission, latest_analysis=None) -> SubmissionDetailResponse:
    analysis_resp = None
    if latest_analysis:
        result_dict = latest_analysis.result if isinstance(latest_analysis.result, dict) else {}
        analysis_resp = AnalysisDetailResponse(
            id=latest_analysis.id,
            tier_used=latest_analysis.tier_used,
            model_used=latest_analysis.model_used,
            degraded=getattr(latest_analysis, "degraded", False),
            degradation_reason=getattr(latest_analysis, "degradation_reason", None),
            total_latency_ms=getattr(latest_analysis, "total_latency_ms", 0),
            result=AnalysisResult(**result_dict),
            created_at=latest_analysis.created_at,
        )
    return SubmissionDetailResponse(
        id=submission.id,
        title=submission.title,
        brief_text=submission.brief_text,
        asset_urls=submission.asset_urls or [],
        tier_requested=submission.tier_requested,
        status=submission.status,
        created_at=submission.created_at,
        analysis=analysis_resp,
    )


@router.post("", response_model=SubmissionDetailResponse, status_code=status.HTTP_200_OK)
async def create_submission(
    body: SubmissionCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    correlation_id = correlation_id_var.get() or str(uuid.uuid4())
    strategy = request.app.state.strategy
    tier_router: TierRouter = request.app.state.tier_router

    sub_repo = SubmissionRepository(db)
    submission = await sub_repo.create(body)

    tier, routing_reason = tier_router.select_tier(body)
    logger.info(
        "tier_routed",
        submission_id=submission.id,
        tier_requested=body.tier_override,
        tier_selected=tier,
        routing_reason=routing_reason,
        content_type=body.content_type,
        asset_count=len(body.asset_urls),
        brief_length=len(body.brief_text or ""),
        source="api",
        correlation_id=correlation_id,
    )

    if tier == "fast":
        from app.assets.fetcher import fetch_images
        from app.config import get_settings
        settings = get_settings()
        try:
            images = await fetch_images(body.asset_urls, settings.image_max_bytes) if body.asset_urls else []
            outcome = await strategy.run_fast(
                brief=body.brief_text or "",
                images=images,
                correlation_id=correlation_id,
            )
            metadata = outcome.processing_metadata
            metadata["routing_reason"] = routing_reason
            ana_repo = AnalysisRepository(db)
            analysis = await ana_repo.create(
                submission_id=submission.id,
                tier_used=outcome.tier_used,
                model_used=outcome.model_used,
                result=outcome.result,
                total_latency_ms=outcome.total_latency_ms,
                degraded=outcome.degraded,
                degradation_reason=outcome.degradation_reason,
                processing_metadata=metadata,
            )
            await sub_repo.update_status(submission.id, "done")
            logger.info(
                "fast_analysis_complete",
                submission_id=submission.id,
                model=outcome.model_used,
                degraded=outcome.degraded,
                correlation_id=correlation_id,
            )
            return _to_detail(submission, analysis)

        except Exception as exc:
            await sub_repo.update_status(submission.id, "failed")
            logger.error("fast_analysis_failed", error=str(exc), correlation_id=correlation_id)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Analysis failed")

    else:
        background_tasks.add_task(
            run_analysis,
            submission_id=submission.id,
            strategy=strategy,
            tier_router=tier_router,
            correlation_id=correlation_id,
        )
        logger.info("rich_analysis_queued", submission_id=submission.id, correlation_id=correlation_id)
        return _to_detail(submission)


@router.get("", response_model=list[SubmissionSummaryResponse])
async def list_submissions(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    repo = SubmissionRepository(db)
    submissions = await repo.list(limit=min(limit, 100), offset=offset)
    return [
        SubmissionSummaryResponse(
            id=s.id,
            title=s.title,
            status=s.status,
            tier_requested=s.tier_requested,
            created_at=s.created_at,
        )
        for s in submissions
    ]


@router.get("/{submission_id}", response_model=SubmissionDetailResponse)
async def get_submission(
    submission_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    repo = SubmissionRepository(db)
    submission = await repo.get(submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    ana_repo = AnalysisRepository(db)
    latest_analysis = await ana_repo.get_latest_by_submission(submission_id)
    return _to_detail(submission, latest_analysis)


@router.get("/{submission_id}/audit", response_model=AuditResponse)
async def get_submission_audit(
    submission_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    sub_repo = SubmissionRepository(db)
    submission = await sub_repo.get(submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

    ana_repo = AnalysisRepository(db)
    latest = await ana_repo.get_latest_by_submission(submission_id)
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No analysis found")

    return AuditResponse(
        submission_id=submission_id,
        analysis_id=latest.id,
        processing_metadata=latest.processing_metadata,
    )

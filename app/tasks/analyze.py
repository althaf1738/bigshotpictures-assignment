from __future__ import annotations

from app.assets.fetcher import fetch_images
from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.observability.logging import correlation_id_var, get_logger
from app.repositories.analyses import AnalysisRepository
from app.repositories.submissions import SubmissionRepository
from app.routing.router import TierRouter
from app.schemas.submission import SubmissionCreate

logger = get_logger(__name__)


async def run_analysis(
    submission_id: str,
    strategy,
    tier_router: TierRouter,
    correlation_id: str,
) -> None:
    token = correlation_id_var.set(correlation_id)
    try:
        await _run_inner(submission_id, strategy, tier_router, correlation_id)
    except Exception as exc:
        logger.error("run_analysis_unhandled", submission_id=submission_id, error=str(exc), correlation_id=correlation_id)
    finally:
        correlation_id_var.reset(token)


async def _run_inner(
    submission_id: str,
    strategy,
    tier_router: TierRouter,
    correlation_id: str,
) -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        sub_repo = SubmissionRepository(session)
        ana_repo = AnalysisRepository(session)

        submission = await sub_repo.get(submission_id)
        if not submission:
            logger.error("submission_not_found", submission_id=submission_id)
            return

        await sub_repo.update_status(submission_id, "processing")

        fake_create = SubmissionCreate(
            title=submission.title,
            brief_text=submission.brief_text,
            asset_urls=submission.asset_urls or [],
            tier_override=submission.tier_requested,
            content_type=submission.content_type,
        )
        tier, routing_reason = tier_router.select_tier(fake_create)
        logger.info(
            "tier_routed",
            submission_id=submission_id,
            tier_requested=submission.tier_requested,
            tier_selected=tier,
            routing_reason=routing_reason,
            content_type=submission.content_type,
            asset_count=len(submission.asset_urls or []),
            brief_length=len(submission.brief_text or ""),
            source="background",
            correlation_id=correlation_id,
        )
        logger.info(
            "analysis_started",
            submission_id=submission_id,
            tier_selected=tier,
            routing_reason=routing_reason,
            correlation_id=correlation_id,
        )

        images = []
        if submission.asset_urls:
            images = await fetch_images(submission.asset_urls, settings.image_max_bytes)

        try:
            if tier == "fast":
                outcome = await strategy.run_fast(
                    brief=submission.brief_text or "",
                    images=images,
                    correlation_id=correlation_id,
                )
            else:
                outcome = await strategy.run_rich(
                    brief=submission.brief_text or "",
                    images=images,
                    correlation_id=correlation_id,
                )

            metadata = outcome.processing_metadata
            metadata["routing_reason"] = routing_reason
            await ana_repo.create(
                submission_id=submission_id,
                tier_used=outcome.tier_used,
                model_used=outcome.model_used,
                result=outcome.result,
                total_latency_ms=outcome.total_latency_ms,
                degraded=outcome.degraded,
                degradation_reason=outcome.degradation_reason,
                processing_metadata=metadata,
            )
            await sub_repo.update_status(submission_id, "done")
            logger.info(
                "analysis_complete",
                submission_id=submission_id,
                tier=outcome.tier_used,
                model=outcome.model_used,
                degraded=outcome.degraded,
                latency_ms=outcome.total_latency_ms,
                correlation_id=correlation_id,
            )

        except Exception as exc:
            await sub_repo.update_status(submission_id, "failed")
            logger.error(
                "analysis_failed",
                submission_id=submission_id,
                error=str(exc),
                correlation_id=correlation_id,
            )

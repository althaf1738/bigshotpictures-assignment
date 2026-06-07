from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.analysis.exceptions import PoolExhaustedError, SchemaValidationError
from app.analysis.pool import CallPayload, ModelPool, PoolAttemptRecord
from app.observability.logging import get_logger
from app.schemas.analysis import AnalysisResult, CreativeScore

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_MOCK_RESULT = AnalysisResult(
    summary="Mock analysis: service degraded — creative submission noted for manual review.",
    scores=[
        CreativeScore(dimension="concept", score=7, rationale="Concept received"),
        CreativeScore(dimension="execution", score=7, rationale="Execution noted"),
        CreativeScore(dimension="audience_fit", score=7, rationale="Audience alignment noted"),
        CreativeScore(dimension="brand_alignment", score=7, rationale="Brand noted"),
        CreativeScore(dimension="originality", score=7, rationale="Originality noted"),
        CreativeScore(dimension="impact", score=7, rationale="Impact noted"),
    ],
    strengths=["Submission received"],
    improvements=["Full analysis unavailable — please retry"],
    target_audience="Unknown",
    tone="Unknown",
    recommendations=["Retry analysis when service is available"],
    confidence=0.0,
    tier_used="mock",
    model_used="mock",
)

_ANALYSIS_RESULT_KEYS = {
    "summary",
    "scores",
    "strengths",
    "improvements",
    "target_audience",
    "tone",
    "recommendations",
    "confidence",
}


@dataclass
class StrategyResult:
    result: AnalysisResult
    requested_tier: str
    tier_used: str
    stages_completed: list[str]
    pool_attempts: list[PoolAttemptRecord]
    total_latency_ms: int
    degraded: bool
    degradation_reason: str | None
    model_used: str

    @property
    def processing_metadata(self) -> dict:
        return {
            "requested_tier": self.requested_tier,
            "served_tier": self.tier_used,
            "stages_completed": self.stages_completed,
            "pool_attempts": [asdict(a) for a in self.pool_attempts],
            "total_latency_ms": self.total_latency_ms,
            "degraded": self.degraded,
            "degradation_reason": self.degradation_reason,
        }


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _extract_json(text: str) -> dict:
    decoder = json.JSONDecoder()

    stripped = text.strip()
    if stripped.startswith("{"):
        parsed, _ = decoder.raw_decode(stripped)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("Expected a JSON object", text, 0)
        return parsed

    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and _ANALYSIS_RESULT_KEYS.issubset(parsed):
            return parsed

    raise json.JSONDecodeError("No complete AnalysisResult JSON object found", text, 0)


def _validate_schema(text: str) -> None:
    """Raise SchemaValidationError if `text` does not parse into an AnalysisResult.

    Used by ModelPool.call as a gate so a member is only considered
    successful once its response is actually usable — keeping
    pool_member_success/pool_attempts consistent with the degraded flag.
    """
    try:
        data = _extract_json(text)
        AnalysisResult(**data)
    except (json.JSONDecodeError, ValidationError, KeyError) as e:
        raise SchemaValidationError(str(e), raw_response=text) from e


def _parse_result(text: str, tier_used: str, model_used: str) -> AnalysisResult:
    try:
        data = _extract_json(text)
        data.setdefault("tier_used", tier_used)
        data.setdefault("model_used", model_used)
        return AnalysisResult(**data)
    except (json.JSONDecodeError, ValidationError, KeyError) as e:
        raise SchemaValidationError(str(e), raw_response=text) from e


def _log_stage_failure(event: str, exc: Exception, correlation_id: str) -> None:
    fields = {
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "correlation_id": correlation_id,
    }
    raw_response = getattr(exc, "raw_response", None)
    if raw_response:
        fields["raw_response_preview"] = raw_response[:500]
    logger.warning(event, **fields)


def _extend_attempts_from_error(
    target: list[PoolAttemptRecord],
    exc: Exception,
) -> None:
    attempts = getattr(exc, "attempts", None)
    if attempts:
        target.extend(attempts)


class AnalysisStrategy:
    def __init__(self, fast_pool: ModelPool, rich_pool: ModelPool) -> None:
        self._fast_pool = fast_pool
        self._rich_pool = rich_pool

    # ------------------------------------------------------------------ #
    # Public entry points                                                  #
    # ------------------------------------------------------------------ #

    async def run_fast(
        self,
        brief: str,
        images: list[bytes],
        correlation_id: str,
    ) -> StrategyResult:
        start = time.monotonic()
        all_attempts: list[PoolAttemptRecord] = []

        payload = CallPayload(
            system=_load_prompt("fast_tier.md"),
            user_text=brief,
            images=images,
            stage="single",
        )
        try:
            text, attempts, model_used = await self._fast_pool.call(
                payload, correlation_id=correlation_id, validate=_validate_schema,
            )
            all_attempts.extend(attempts)
            result = _parse_result(text, tier_used="fast", model_used=model_used)
            total_ms = int((time.monotonic() - start) * 1000)
            return StrategyResult(
                result=result,
                requested_tier="fast",
                tier_used="fast",
                stages_completed=["single"],
                pool_attempts=all_attempts,
                total_latency_ms=total_ms,
                degraded=False,
                degradation_reason=None,
                model_used=model_used,
            )
        except (PoolExhaustedError, SchemaValidationError) as exc:
            _extend_attempts_from_error(all_attempts, exc)
            return self._mock_outcome("fast", "fast_pool_exhausted", start, all_attempts)

    async def run_rich(
        self,
        brief: str,
        images: list[bytes],
        correlation_id: str,
    ) -> StrategyResult:
        start = time.monotonic()
        all_attempts: list[PoolAttemptRecord] = []

        # ---- Stage 1: extract ----
        stage1_payload = CallPayload(
            system=_load_prompt("extract_stage.md"),
            user_text=brief,
            images=images,
            stage="stage_1",
        )
        try:
            stage1_text, attempts, _ = await self._rich_pool.call(stage1_payload, correlation_id=correlation_id)
            all_attempts.extend(attempts)
        except (PoolExhaustedError, SchemaValidationError) as exc:
            _extend_attempts_from_error(all_attempts, exc)
            _log_stage_failure("stage_1_failed", exc, correlation_id)
            return await self._fast_pool_fallback(
                brief, images,
                success_reason="stage_1_failed_recovered_via_fast_pool",
                fail_reason="stage_1_failed_fast_pool_also_exhausted",
                start=start,
                prior_attempts=all_attempts,
                correlation_id=correlation_id,
            )

        # ---- Stage 2: evaluate ----
        stage2_payload = CallPayload(
            system=_load_prompt("evaluate_stage.md"),
            user_text=brief,
            images=images,
            stage="stage_2",
            prior_assistant=stage1_text,
            follow_up_text="Now perform the full creative evaluation based on your extraction above.",
        )
        try:
            text, attempts, model_used = await self._rich_pool.call(
                stage2_payload, correlation_id=correlation_id, validate=_validate_schema,
            )
            all_attempts.extend(attempts)
            result = _parse_result(text, tier_used="rich", model_used=model_used)
            total_ms = int((time.monotonic() - start) * 1000)
            return StrategyResult(
                result=result,
                requested_tier="rich",
                tier_used="rich",
                stages_completed=["stage_1", "stage_2"],
                pool_attempts=all_attempts,
                total_latency_ms=total_ms,
                degraded=False,
                degradation_reason=None,
                model_used=model_used,
            )
        except (PoolExhaustedError, SchemaValidationError) as exc:
            _extend_attempts_from_error(all_attempts, exc)
            _log_stage_failure("stage_2_failed", exc, correlation_id)
            return await self._fast_pool_fallback(
                brief, images,
                success_reason="stage_2_failed_recovered_via_fast_pool",
                fail_reason="stage_2_failed_fast_pool_also_exhausted",
                start=start,
                prior_attempts=all_attempts,
                correlation_id=correlation_id,
            )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    async def _fast_pool_fallback(
        self,
        brief: str,
        images: list[bytes],
        success_reason: str,
        fail_reason: str,
        start: float,
        prior_attempts: list[PoolAttemptRecord],
        correlation_id: str,
    ) -> StrategyResult:
        payload = CallPayload(
            system=_load_prompt("fast_tier.md"),
            user_text=brief,
            images=[],  # fast pool members do not require vision
            stage="single",
        )
        try:
            text, attempts, model_used = await self._fast_pool.call(
                payload, correlation_id=correlation_id, validate=_validate_schema,
            )
            prior_attempts.extend(attempts)
            result = _parse_result(text, tier_used="fast", model_used=model_used)
            total_ms = int((time.monotonic() - start) * 1000)
            return StrategyResult(
                result=result,
                requested_tier="rich",
                tier_used="fast",
                stages_completed=["single"],
                pool_attempts=prior_attempts,
                total_latency_ms=total_ms,
                degraded=True,
                degradation_reason=success_reason,
                model_used=model_used,
            )
        except (PoolExhaustedError, SchemaValidationError) as exc:
            _extend_attempts_from_error(prior_attempts, exc)
            return self._mock_outcome("rich", fail_reason, start, prior_attempts)

    def _mock_outcome(
        self,
        requested_tier: str,
        reason: str,
        start: float,
        attempts: list[PoolAttemptRecord],
    ) -> StrategyResult:
        total_ms = int((time.monotonic() - start) * 1000)
        mock = AnalysisResult(**{**_MOCK_RESULT.model_dump(), "tier_used": "mock", "model_used": "mock"})
        return StrategyResult(
            result=mock,
            requested_tier=requested_tier,
            tier_used="mock",
            stages_completed=[],
            pool_attempts=attempts,
            total_latency_ms=total_ms,
            degraded=True,
            degradation_reason=reason,
            model_used="mock",
        )


def build_pool(pool_str: str, settings, pool_name: str, with_thinking: bool = False) -> ModelPool:
    from app.analysis.adapters import load_adapters
    from app.analysis.adapters.registry import create_adapter

    load_adapters()

    members = []
    for entry in pool_str.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            prefix, model_id = entry.split(":", 1)
        except ValueError as exc:
            raise ValueError(f"Invalid pool entry '{entry}'. Expected '<provider>:<model_id>'") from exc
        members.append(create_adapter(prefix, model_id, settings, with_thinking=with_thinking))

    return ModelPool(
        name=pool_name,
        members=members,
        max_attempts=settings.retry_max_attempts,
        initial_wait=settings.retry_initial_wait_s,
        max_wait=settings.retry_max_wait_s,
    )


def create_strategy(settings) -> AnalysisStrategy:
    fast_pool = build_pool(settings.fast_pool, settings, pool_name="fast")
    rich_pool = build_pool(settings.rich_pool, settings, pool_name="rich", with_thinking=True)
    return AnalysisStrategy(fast_pool=fast_pool, rich_pool=rich_pool)

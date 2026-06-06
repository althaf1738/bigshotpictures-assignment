from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.analysis.exceptions import PoolExhaustedError, RetryableError, SchemaValidationError
from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CallPayload:
    system: str
    user_text: str
    images: list[bytes] = field(default_factory=list)
    stage: str = "single"          # "single" | "stage_1" | "stage_2"
    prior_assistant: str | None = None
    follow_up_text: str | None = None


@dataclass
class PoolAttemptRecord:
    pool: str
    model: str
    stage: str
    outcome: str
    latency_ms: int
    retries: int
    error_type: str | None = None
    error_message: str | None = None


def _outcome_for(exc: Exception) -> str:
    name = type(exc).__name__
    mapping = {
        "ProviderUnavailableError": "provider_unavailable",
        "RateLimitError": "rate_limit",
        "AuthError": "auth_error",
        "BadRequestError": "bad_request",
        "ContentPolicyError": "content_policy",
    }
    return mapping.get(name, name.lower())


class ModelPool:
    def __init__(
        self,
        name: str,
        members: list,
        max_attempts: int = 3,
        initial_wait: float = 1.0,
        max_wait: float = 10.0,
    ) -> None:
        self.name = name
        self._members = members
        self._max_attempts = max_attempts
        self._initial_wait = initial_wait
        self._max_wait = max_wait

    async def call(
        self,
        payload: CallPayload,
        correlation_id: str = "",
        validate: Callable[[str], None] | None = None,
    ) -> tuple[str, list[PoolAttemptRecord], str]:
        """Try each member in order.

        If `validate` is provided, it is called with the raw response text
        before the attempt is considered successful. A `SchemaValidationError`
        raised by `validate` is treated as a failed attempt for that member
        (the pool moves on to the next member) — this keeps the
        `pool_member_success` log and the persisted `pool_attempts` audit
        trail consistent with whether a usable result was actually produced.

        Returns (response_text, attempt_records, model_label).
        Raises PoolExhaustedError if every member fails.
        """
        attempts: list[PoolAttemptRecord] = []

        for member in self._members:
            model_label = f"{getattr(member, 'provider_prefix', 'unknown')}:{member.model_id}"
            start = time.monotonic()

            retrying = AsyncRetrying(
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(min=self._initial_wait, max=self._max_wait),
                retry=retry_if_exception_type(RetryableError),
                reraise=True,
            )
            try:
                result = await retrying(member.call, payload)
            except Exception as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                retries = retrying.statistics.get("attempt_number", 1) - 1
                outcome = _outcome_for(exc)
                attempts.append(PoolAttemptRecord(
                    pool=self.name,
                    model=model_label,
                    stage=payload.stage,
                    outcome=outcome,
                    latency_ms=latency_ms,
                    retries=retries,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ))
                logger.warning(
                    "pool_member_failed",
                    pool=self.name,
                    model=model_label,
                    stage=payload.stage,
                    outcome=outcome,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    retries=retries,
                    correlation_id=correlation_id,
                )
                continue

            latency_ms = int((time.monotonic() - start) * 1000)
            retries = retrying.statistics.get("attempt_number", 1) - 1

            if validate is not None:
                try:
                    validate(result)
                except SchemaValidationError as verr:
                    attempts.append(PoolAttemptRecord(
                        pool=self.name,
                        model=model_label,
                        stage=payload.stage,
                        outcome="schema_validation_failed",
                        latency_ms=latency_ms,
                        retries=retries,
                        error_type=type(verr).__name__,
                        error_message=str(verr),
                    ))
                    logger.warning(
                        "pool_member_failed",
                        pool=self.name,
                        model=model_label,
                        stage=payload.stage,
                        outcome="schema_validation_failed",
                        error_type=type(verr).__name__,
                        error_message=str(verr),
                        raw_response_preview=result[:500],
                        retries=retries,
                        correlation_id=correlation_id,
                    )
                    continue

            attempts.append(PoolAttemptRecord(
                pool=self.name,
                model=model_label,
                stage=payload.stage,
                outcome="success",
                latency_ms=latency_ms,
                retries=retries,
            ))
            logger.info(
                "pool_member_success",
                pool=self.name,
                model=model_label,
                stage=payload.stage,
                latency_ms=latency_ms,
                retries=retries,
                correlation_id=correlation_id,
            )
            return result, attempts, model_label

        raise PoolExhaustedError(f"All members of pool '{self.name}' failed", attempts=attempts)

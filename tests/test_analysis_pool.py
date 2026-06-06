from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from app.analysis.exceptions import (
    AuthError, PoolExhaustedError, ProviderUnavailableError, RateLimitError,
)
from app.analysis.pool import CallPayload, ModelPool
from app.analysis.strategy import _validate_schema


class _OkAdapter:
    provider_prefix = "mock"
    model_id = "ok-model"

    async def call(self, payload: CallPayload) -> str:
        return '{"summary":"ok","scores":[],"strengths":[],"improvements":[],"target_audience":"","tone":"","recommendations":[],"confidence":0.9,"tier_used":"fast","model_used":"ok-model"}'


class _FailThenSucceedAdapter:
    """Fails once with a retryable error then succeeds."""
    provider_prefix = "mock"
    model_id = "flaky-model"

    def __init__(self):
        self._calls = 0

    async def call(self, payload: CallPayload) -> str:
        self._calls += 1
        if self._calls == 1:
            raise ProviderUnavailableError("transient")
        return '{"summary":"ok","scores":[],"strengths":[],"improvements":[],"target_audience":"","tone":"","recommendations":[],"confidence":0.8,"tier_used":"fast","model_used":"flaky-model"}'


class _AlwaysFailAdapter:
    provider_prefix = "mock"
    model_id = "dead-model"

    def __init__(self):
        self.calls = 0

    async def call(self, payload: CallPayload) -> str:
        self.calls += 1
        raise ProviderUnavailableError("always down")


class _MalformedJsonAdapter:
    """Returns a successful HTTP response whose body is not valid AnalysisResult JSON."""
    provider_prefix = "mock"
    model_id = "malformed-model"

    async def call(self, payload: CallPayload) -> str:
        return "this is not json at all {definitely not}"


class _AuthFailAdapter:
    """Raises a non-retryable AuthError — should NOT be retried."""
    provider_prefix = "mock"
    model_id = "auth-fail-model"

    def __init__(self):
        self.calls = 0

    async def call(self, payload: CallPayload) -> str:
        self.calls += 1
        raise AuthError("bad key")


def _payload():
    return CallPayload(system="sys", user_text="brief", stage="single")


async def test_pool_succeeds_on_first_member():
    pool = ModelPool("test", [_OkAdapter()], max_attempts=3)
    text, attempts, model = await pool.call(_payload())
    assert text
    assert len(attempts) == 1
    assert attempts[0].outcome == "success"
    assert model == "mock:ok-model"


async def test_pool_succeeds_on_second_when_first_fails():
    pool = ModelPool("test", [_AlwaysFailAdapter(), _OkAdapter()], max_attempts=1)
    text, attempts, model = await pool.call(_payload())
    assert text
    assert len(attempts) == 2
    assert attempts[0].outcome != "success"
    assert attempts[1].outcome == "success"
    assert model == "mock:ok-model"


async def test_pool_raises_exhausted_when_all_fail():
    pool = ModelPool("test", [_AlwaysFailAdapter(), _AlwaysFailAdapter()], max_attempts=1)
    with pytest.raises(PoolExhaustedError) as exc_info:
        await pool.call(_payload())
    assert len(exc_info.value.attempts) == 2
    assert exc_info.value.attempts[0].outcome == "provider_unavailable"
    assert exc_info.value.attempts[1].outcome == "provider_unavailable"


async def test_pool_retries_retryable_error():
    adapter = _FailThenSucceedAdapter()
    pool = ModelPool("test", [adapter], max_attempts=3, initial_wait=0.0, max_wait=0.0)
    text, attempts, _ = await pool.call(_payload())
    assert text
    assert adapter._calls == 2
    assert attempts[0].retries == 1


async def test_pool_respects_max_attempts_for_retryable_errors():
    adapter = _AlwaysFailAdapter()
    pool = ModelPool("test", [adapter], max_attempts=3, initial_wait=0.0, max_wait=0.0)
    with pytest.raises(PoolExhaustedError):
        await pool.call(_payload())
    assert adapter.calls == 3


async def test_schema_validation_failure_logs_failed_not_success_and_tries_next_member():
    """A successful HTTP response with malformed JSON must not be logged/recorded as success."""
    pool = ModelPool("test", [_MalformedJsonAdapter(), _OkAdapter()], max_attempts=1)

    with capture_logs() as logs:
        text, attempts, model = await pool.call(_payload(), validate=_validate_schema)

    assert text
    assert model == "mock:ok-model"

    # pool_member_success must NOT be emitted for the malformed-JSON member
    success_events = [e for e in logs if e.get("event") == "pool_member_success"]
    assert len(success_events) == 1
    assert success_events[0]["model"] == "mock:ok-model"

    # pool_member_failed IS emitted with schema-validation details
    failed_events = [e for e in logs if e.get("event") == "pool_member_failed"]
    assert len(failed_events) == 1
    failed = failed_events[0]
    assert failed["model"] == "mock:malformed-model"
    assert failed["outcome"] == "schema_validation_failed"
    assert failed["error_type"] == "SchemaValidationError"
    assert failed["error_message"]
    assert "raw_response_preview" in failed
    assert failed["raw_response_preview"] == "this is not json at all {definitely not}"[:500]

    # The attempt record persisted to processing_metadata.pool_attempts
    assert len(attempts) == 2
    assert attempts[0].outcome == "schema_validation_failed"
    assert attempts[0].error_type == "SchemaValidationError"
    assert attempts[0].error_message
    assert attempts[1].outcome == "success"
    assert attempts[1].error_type is None


async def test_pool_does_not_retry_non_retryable():
    adapter = _AuthFailAdapter()
    # Even with max_attempts=3, AuthError should NOT be retried
    pool = ModelPool("test", [adapter], max_attempts=3, initial_wait=0.0, max_wait=0.0)
    with pytest.raises(PoolExhaustedError):
        await pool.call(_payload())
    assert adapter.calls == 1  # called exactly once, no retries

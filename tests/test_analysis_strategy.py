from __future__ import annotations

import json
import pytest
from types import SimpleNamespace

from app.analysis.exceptions import PoolExhaustedError
from app.analysis.pool import CallPayload, ModelPool
from app.analysis.strategy import AnalysisStrategy, build_pool, _extract_json


_VALID_JSON = json.dumps({
    "summary": "Great ad",
    "scores": [{"dimension": "concept", "score": 8, "rationale": "Good"}],
    "strengths": ["Clear"],
    "improvements": ["Better headline"],
    "target_audience": "Adults",
    "tone": "Energetic",
    "recommendations": ["Test variants"],
    "confidence": 0.9,
})


class _SuccessAdapter:
    provider_prefix = "mock"
    model_id = "success-model"

    async def call(self, payload: CallPayload) -> str:
        return _VALID_JSON


class _FailAdapter:
    provider_prefix = "mock"
    model_id = "fail-model"

    async def call(self, payload: CallPayload) -> str:
        raise PoolExhaustedError("always fails")


def _make_pool(adapters, name="test") -> ModelPool:
    return ModelPool(name=name, members=adapters, max_attempts=1)


def _make_strategy(fast_adapters, rich_adapters) -> AnalysisStrategy:
    return AnalysisStrategy(
        fast_pool=_make_pool(fast_adapters, "fast"),
        rich_pool=_make_pool(rich_adapters, "rich"),
    )


def _settings():
    return SimpleNamespace(
        retry_max_attempts=1,
        retry_initial_wait_s=0.0,
        retry_max_wait_s=0.0,
    )


def test_build_pool_rejects_malformed_entries():
    with pytest.raises(ValueError, match="Expected '<provider>:<model_id>'"):
        build_pool("bad-entry", _settings(), "fast")


def test_build_pool_rejects_unknown_provider_prefix():
    with pytest.raises(ValueError, match="Unknown provider prefix 'missing'"):
        build_pool("missing:model", _settings(), "fast")


def test_extract_json_recovers_last_object_from_wrapped_model_output():
    text = f'thinking {{"scratch": true}}\n```json\n{_VALID_JSON}\n```'
    assert _extract_json(text)["summary"] == "Great ad"


def test_extract_json_does_not_return_nested_object_from_truncated_result():
    text = '''{
      "summary": "A valid-looking start",
      "scores": [
        {"dimension": "concept", "score": 4, "rationale": "Good"},
        {"dimension": "execution", "score": 5, "rationale": "Also good"}
    '''
    with pytest.raises(json.JSONDecodeError, match="No complete AnalysisResult|Expecting"):
        _extract_json(text)


# --- Fast tier ---

async def test_fast_happy_path():
    strategy = _make_strategy(fast_adapters=[_SuccessAdapter()], rich_adapters=[])
    outcome = await strategy.run_fast("brief", [], "corr-1")
    assert outcome.degraded is False
    assert outcome.tier_used == "fast"
    assert outcome.result.summary == "Great ad"
    assert outcome.stages_completed == ["single"]


async def test_fast_pool_exhausted_returns_mock():
    strategy = _make_strategy(fast_adapters=[_FailAdapter()], rich_adapters=[])
    outcome = await strategy.run_fast("brief", [], "corr-2")
    assert outcome.degraded is True
    assert outcome.degradation_reason == "fast_pool_exhausted"
    assert outcome.result.model_used == "mock"


# --- Rich tier ---

async def test_rich_happy_path():
    strategy = _make_strategy(fast_adapters=[], rich_adapters=[_SuccessAdapter()])
    outcome = await strategy.run_rich("brief", [], "corr-3")
    assert outcome.degraded is False
    assert outcome.tier_used == "rich"
    assert "stage_1" in outcome.stages_completed
    assert "stage_2" in outcome.stages_completed


async def test_rich_stage1_failure_falls_back_to_fast_pool():
    strategy = _make_strategy(
        fast_adapters=[_SuccessAdapter()],
        rich_adapters=[_FailAdapter()],
    )
    outcome = await strategy.run_rich("brief", [], "corr-4")
    assert outcome.degraded is True
    assert outcome.degradation_reason == "stage_1_failed_recovered_via_fast_pool"
    assert outcome.tier_used == "fast"


async def test_rich_stage1_failure_fast_pool_also_fails_returns_mock():
    strategy = _make_strategy(
        fast_adapters=[_FailAdapter()],
        rich_adapters=[_FailAdapter()],
    )
    outcome = await strategy.run_rich("brief", [], "corr-5")
    assert outcome.degraded is True
    assert outcome.degradation_reason == "stage_1_failed_fast_pool_also_exhausted"
    assert outcome.result.model_used == "mock"


async def test_rich_stage2_failure_falls_back_to_fast_pool():
    call_count = [0]

    class _Stage1OkStage2FailAdapter:
        provider_prefix = "mock"
        model_id = "stage2-fail"

        async def call(self, payload: CallPayload) -> str:
            call_count[0] += 1
            if payload.stage == "stage_1":
                return "extraction text"
            raise PoolExhaustedError("stage 2 fail")

    strategy = _make_strategy(
        fast_adapters=[_SuccessAdapter()],
        rich_adapters=[_Stage1OkStage2FailAdapter()],
    )
    outcome = await strategy.run_rich("brief", [], "corr-6")
    assert outcome.degraded is True
    assert outcome.degradation_reason == "stage_2_failed_recovered_via_fast_pool"
    assert [a.stage for a in outcome.pool_attempts] == ["stage_1", "stage_2", "single"]
    assert outcome.pool_attempts[1].outcome == "poolexhaustederror"
    assert outcome.pool_attempts[1].error_message == "stage 2 fail"


async def test_rich_stage2_failure_fast_pool_also_fails_returns_mock():
    class _Stage1OkStage2FailAdapter:
        provider_prefix = "mock"
        model_id = "stage2-fail"

        async def call(self, payload: CallPayload) -> str:
            if payload.stage == "stage_1":
                return "extraction text"
            raise PoolExhaustedError("stage 2 fail")

    strategy = _make_strategy(
        fast_adapters=[_FailAdapter()],
        rich_adapters=[_Stage1OkStage2FailAdapter()],
    )
    outcome = await strategy.run_rich("brief", [], "corr-7")
    assert outcome.degraded is True
    assert outcome.degradation_reason == "stage_2_failed_fast_pool_also_exhausted"
    assert outcome.result.model_used == "mock"

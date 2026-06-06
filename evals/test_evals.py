"""Evaluation harness for the Creative Review Analysis API.

Run with:
  EVAL_PROVIDER=mock pytest evals/test_evals.py -v
  EVAL_PROVIDER=strategy pytest evals/test_evals.py -v  # uses configured pools / real APIs
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Protocol

import pytest
from pydantic import ValidationError

from app.analysis.strategy import create_strategy
from app.assets.fetcher import fetch_images
from app.config import get_settings
from app.routing.router import TierRouter
from app.schemas.analysis import AnalysisResult, CreativeScore
from app.schemas.submission import SubmissionCreate

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES = sorted(FIXTURES_DIR.glob("*.json"))
EXPECTED_SCORE_DIMENSIONS = {
    "concept",
    "execution",
    "audience_fit",
    "brand_alignment",
    "originality",
    "impact",
}

# 1x1 JPEG. Used so asset-routing evals exercise image-bearing payloads without
# depending on external URLs in the default mock/CI path.
TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00" + bytes([0] * 64) +
    b"\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x01\x00\x00\xff\xc4\x00\x14"
    b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xff\xd9"
)


class EvalRunner(Protocol):
    async def analyze(
        self,
        brief: str,
        images: list[bytes],
        tier: str,
        correlation_id: str,
    ) -> AnalysisResult: ...


class StrategyEvalRunner:
    def __init__(self) -> None:
        self._strategy = create_strategy(get_settings())

    async def analyze(
        self,
        brief: str,
        images: list[bytes],
        tier: str,
        correlation_id: str,
    ) -> AnalysisResult:
        if tier == "rich":
            outcome = await self._strategy.run_rich(brief, images, correlation_id)
        else:
            outcome = await self._strategy.run_fast(brief, images, correlation_id)
        return outcome.result


class MockEvalRunner:
    async def analyze(
        self,
        brief: str,
        images: list[bytes],
        tier: str,
        correlation_id: str,
    ) -> AnalysisResult:
        return AnalysisResult(
            summary="Mock analysis: the creative submission shows strong concept alignment with clear messaging.",
            scores=[
                CreativeScore(dimension="concept", score=8, rationale="Clear and compelling core idea"),
                CreativeScore(dimension="execution", score=7, rationale="Well-structured presentation"),
                CreativeScore(dimension="audience_fit", score=8, rationale="Targets the right demographic"),
                CreativeScore(dimension="brand_alignment", score=9, rationale="Consistent brand voice"),
                CreativeScore(dimension="originality", score=7, rationale="Fresh perspective on familiar theme"),
                CreativeScore(dimension="impact", score=8, rationale="Strong emotional resonance"),
            ],
            strengths=["Clear messaging", "Strong visual hierarchy", "Compelling call to action"],
            improvements=["Consider more diverse imagery", "Strengthen the headline", "Clarify the value proposition"],
            target_audience="Young professionals aged 25-40",
            tone="Professional and energetic",
            recommendations=[
                "Test A/B variants for the headline",
                "Add social proof elements",
                "Optimize for mobile viewing",
            ],
            confidence=0.85,
            tier_used=tier,
            model_used="mock",
        )


def _load_fixtures():
    return [json.loads(f.read_text()) for f in FIXTURES]


def _get_provider() -> EvalRunner:
    eval_provider = os.environ.get("EVAL_PROVIDER", "mock")
    if eval_provider == "strategy":
        return StrategyEvalRunner()
    return MockEvalRunner()


async def _images_for_fixture(sub_data: dict) -> list[bytes]:
    asset_urls = sub_data.get("asset_urls", [])
    if not asset_urls:
        return []
    if os.environ.get("EVAL_FETCH_ASSETS") == "1":
        return await fetch_images(asset_urls, get_settings().image_max_bytes)
    return [TINY_JPEG for _ in asset_urls]


def _validate_fixture_schema(fixture: dict) -> None:
    assert isinstance(fixture.get("id"), str) and fixture["id"], "Fixture id is required"
    assert isinstance(fixture.get("description"), str) and fixture["description"], (
        f"[{fixture.get('id')}] description is required"
    )
    assert isinstance(fixture.get("submission"), dict), f"[{fixture['id']}] submission must be an object"
    assert isinstance(fixture.get("expected"), dict), f"[{fixture['id']}] expected must be an object"

    sub_data = fixture["submission"]
    assert isinstance(sub_data.get("title"), str) and sub_data["title"], (
        f"[{fixture['id']}] submission.title is required"
    )
    assert sub_data.get("tier", "auto") in {"auto", "fast", "rich"}, (
        f"[{fixture['id']}] submission.tier must be auto, fast, or rich"
    )
    assert isinstance(sub_data.get("asset_urls", []), list), (
        f"[{fixture['id']}] submission.asset_urls must be a list"
    )

    try:
        SubmissionCreate(
            title=sub_data["title"],
            brief_text=sub_data.get("brief_text"),
            asset_urls=sub_data.get("asset_urls", []),
            tier_override=sub_data.get("tier", "auto"),
            content_type=sub_data.get("content_type", "auto"),
        )
    except ValidationError as exc:
        raise AssertionError(f"[{fixture['id']}] invalid submission fixture: {exc}") from exc

    expected = fixture["expected"]
    if "expected_tier" in expected:
        assert expected["expected_tier"] in {"fast", "rich"}, (
            f"[{fixture['id']}] expected.expected_tier must be fast or rich"
        )
    if "score_range" in expected:
        assert expected["score_range"] == [1, 10], (
            f"[{fixture['id']}] score_range should remain the API contract [1, 10]"
        )


def _record_eval_result(fixture_id: str, tier: str, result: AnalysisResult, duration_ms: float) -> None:
    output_dir = os.environ.get("EVAL_RESULTS_DIR")
    if not output_dir:
        return
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "fixture_id": fixture_id,
        "tier": tier,
        "duration_ms": round(duration_ms, 2),
        "result": result.model_dump(mode="json"),
    }
    (path / f"{fixture_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


FIXTURE_DATA = _load_fixtures()


def test_fixture_files_are_valid():
    assert FIXTURES, "No eval fixtures found"
    ids = [fixture.get("id") for fixture in FIXTURE_DATA]
    assert len(ids) == len(set(ids)), "Eval fixture IDs must be unique"
    for fixture in FIXTURE_DATA:
        _validate_fixture_schema(fixture)


@pytest.mark.parametrize("fixture", FIXTURE_DATA, ids=[f["id"] for f in FIXTURE_DATA])
async def test_eval_fixture(fixture: dict):
    provider = _get_provider()
    router = TierRouter()

    sub_data = fixture["submission"]
    expected = fixture["expected"]

    submission = SubmissionCreate(
        title=sub_data["title"],
        brief_text=sub_data.get("brief_text"),
        asset_urls=sub_data.get("asset_urls", []),
        tier_override=sub_data.get("tier", "auto"),
        content_type=sub_data.get("content_type", "auto"),
    )

    # Verify routing
    actual_tier, _routing_reason = router.select_tier(submission)
    if "expected_tier" in expected:
        assert actual_tier == expected["expected_tier"], (
            f"[{fixture['id']}] Expected tier {expected['expected_tier']}, got {actual_tier}"
        )

    # Run analysis
    images = await _images_for_fixture(sub_data)
    start = time.monotonic()
    result = await provider.analyze(
        brief=sub_data.get("brief_text") or "",
        images=images,
        tier=actual_tier,
        correlation_id=fixture["id"],
    )
    duration_ms = (time.monotonic() - start) * 1000

    assert isinstance(result, AnalysisResult), f"[{fixture['id']}] Result must be AnalysisResult"

    # Check required fields
    for field in expected.get("required_fields", []):
        value = getattr(result, field, None)
        assert value is not None and value != "" and value != [], (
            f"[{fixture['id']}] Missing or empty field: {field}"
        )

    # Check confidence threshold
    assert result.confidence >= expected.get("min_confidence", 0.0), (
        f"[{fixture['id']}] Confidence {result.confidence} below minimum {expected['min_confidence']}"
    )

    # Check scores
    score_min, score_max = expected.get("score_range", [1, 10])
    actual_dimensions = {score.dimension for score in result.scores}
    expected_dimensions = set(expected.get("score_dimensions", EXPECTED_SCORE_DIMENSIONS))
    assert actual_dimensions == expected_dimensions, (
        f"[{fixture['id']}] Expected score dimensions {sorted(expected_dimensions)}, "
        f"got {sorted(actual_dimensions)}"
    )
    for score in result.scores:
        assert score_min <= score.score <= score_max, (
            f"[{fixture['id']}] Score {score.score} for '{score.dimension}' out of range [{score_min}, {score_max}]"
        )

    # Check number of scores
    if "num_scores" in expected:
        assert len(result.scores) == expected["num_scores"], (
            f"[{fixture['id']}] Expected {expected['num_scores']} scores, got {len(result.scores)}"
        )

    # Check latency
    if "max_duration_ms" in expected:
        assert duration_ms <= expected["max_duration_ms"], (
            f"[{fixture['id']}] Duration {duration_ms:.0f}ms exceeded {expected['max_duration_ms']}ms"
        )

    _record_eval_result(fixture["id"], actual_tier, result, duration_ms)

    print(f"\n[{fixture['id']}] PASS - tier={actual_tier}, confidence={result.confidence:.2f}, duration={duration_ms:.0f}ms")

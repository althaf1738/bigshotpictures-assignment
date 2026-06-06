from __future__ import annotations

import pytest

from app.routing.router import TierRouter
from app.schemas.submission import SubmissionCreate


@pytest.fixture
def router():
    return TierRouter()


def make_sub(**kwargs) -> SubmissionCreate:
    defaults = {
        "title": "Test",
        "brief_text": None,
        "asset_urls": [],
        "tier_override": "auto",
        "content_type": "auto",
    }
    defaults.update(kwargs)
    return SubmissionCreate(**defaults)


def test_explicit_override_forces_rich(router):
    tier, reason = router.select_tier(make_sub(tier_override="rich"))
    assert tier == "rich"
    assert reason == "user_override"


def test_asset_urls_force_rich(router):
    tier, reason = router.select_tier(make_sub(asset_urls=["https://example.com/a.jpg"]))
    assert tier == "rich"
    assert reason == "has_assets"


def test_rich_content_type_forces_rich(router):
    tier, reason = router.select_tier(make_sub(content_type="creative_brief"))
    assert tier == "rich"
    assert reason == "content_type:creative_brief"


def test_long_text_forces_rich(router):
    text = "x" * 2001
    tier, reason = router.select_tier(make_sub(brief_text=text))
    assert tier == "rich"
    assert reason == "long_text:2001"


def test_keyword_threshold_forces_rich(router):
    text = "Our campaign needs a strong brand positioning and clear messaging."
    tier, reason = router.select_tier(make_sub(brief_text=text))
    assert tier == "rich"
    assert reason.startswith("keyword_match:")
    sample = reason[len("keyword_match:"):]
    assert len(sample.split(",")[0].strip()) > 0


def test_single_keyword_match_stays_fast(router):
    text = "We need a campaign for next quarter."
    tier, reason = router.select_tier(make_sub(brief_text=text))
    assert tier == "fast"
    assert reason == "default_fast"


def test_auto_content_type_no_signals_stays_fast(router):
    tier, reason = router.select_tier(make_sub(brief_text="A short simple product update."))
    assert tier == "fast"
    assert reason == "default_fast"


def test_fast_content_type_with_rich_keywords_escalates_to_rich(router):
    text = "This tagline needs strong brand positioning and clear messaging for launch."
    tier, reason = router.select_tier(make_sub(content_type="tagline", brief_text=text))
    assert tier == "rich"
    assert reason.startswith("keyword_match:")


def test_reason_strings_are_correct_per_rule(router):
    tier, reason = router.select_tier(make_sub(tier_override="rich"))
    assert (tier, reason) == ("rich", "user_override")

    tier, reason = router.select_tier(make_sub(asset_urls=["https://x.com/a.png"]))
    assert (tier, reason) == ("rich", "has_assets")

    tier, reason = router.select_tier(make_sub(content_type="pitch"))
    assert (tier, reason) == ("rich", "content_type:pitch")

    text = "y" * 2500
    tier, reason = router.select_tier(make_sub(brief_text=text))
    assert (tier, reason) == ("rich", "long_text:2500")

    text = "Our campaign objective is to shift brand positioning, messaging, and tone of voice."
    tier, reason = router.select_tier(make_sub(brief_text=text))
    assert tier == "rich"
    assert reason.startswith("keyword_match:")
    matched = reason[len("keyword_match:"):]
    assert "(+" in matched or matched.count(",") <= 2

    tier, reason = router.select_tier(make_sub())
    assert (tier, reason) == ("fast", "default_fast")

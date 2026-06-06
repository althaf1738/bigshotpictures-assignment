from __future__ import annotations

from app.schemas.submission import SubmissionCreate

RICH_CONTENT_TYPES = {
    "creative_brief", "campaign_brief",
    "script_excerpt", "treatment", "pitch",
}

RICH_CONTEXT_KEYWORDS = {
    "campaign", "campaign goal", "objective", "target audience",
    "audience", "brand positioning", "positioning",
    "launch strategy", "go-to-market", "creative direction",
    "creative strategy", "messaging", "message hierarchy",
    "brand voice", "tone of voice", "channels", "distribution",
    "media plan", "deliverables", "timeline", "launch",
    "insight", "consumer insight", "key message",
    "call to action", "cta", "concept", "big idea", "narrative",
    "story", "story arc", "scene", "characters", "script",
    "treatment", "moodboard", "visual direction", "proof points",
    "competitive", "market", "rebrand", "brand guidelines",
    "logo", "color palette", "identity",
}

KEYWORD_MATCH_THRESHOLD = 2


class TierRouter:
    def select_tier(self, submission: SubmissionCreate) -> tuple[str, str]:
        if submission.tier_override == "fast":
            return "fast", "user_override"
        if submission.tier_override == "rich":
            return "rich", "user_override"
        if submission.asset_urls:
            return "rich", "has_assets"
        if submission.content_type in RICH_CONTENT_TYPES:
            return "rich", f"content_type:{submission.content_type}"
        text = submission.brief_text or ""
        if len(text) > 2000:
            return "rich", f"long_text:{len(text)}"
        text_lower = text.lower()
        matched = [kw for kw in RICH_CONTEXT_KEYWORDS if kw in text_lower]
        if len(matched) >= KEYWORD_MATCH_THRESHOLD:
            sample = ", ".join(matched[:3])
            suffix = f" (+{len(matched)-3} more)" if len(matched) > 3 else ""
            return "rich", f"keyword_match:{sample}{suffix}"
        return "fast", "default_fast"

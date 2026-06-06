from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.analysis import AnalysisResult


ContentType = Literal[
    "auto", "tagline", "headline", "social_post", "ad_copy",
    "creative_brief", "concept", "campaign_brief",
    "script_excerpt", "treatment", "pitch",
]


class SubmissionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    brief_text: str | None = None
    asset_urls: list[str] = Field(default_factory=list)
    tier_override: Literal["fast", "rich", "auto"] = "auto"
    content_type: ContentType = "auto"


# --- Response models (one per endpoint shape) ---

class SubmissionSummaryResponse(BaseModel):
    """Returned by GET /submissions (list). No analysis payload."""
    id: str
    title: str
    status: str
    tier_requested: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AnalysisDetailResponse(BaseModel):
    """Embedded in detail and POST responses. No processing_metadata."""
    id: str
    tier_used: str
    model_used: str
    degraded: bool
    degradation_reason: str | None
    total_latency_ms: int
    result: AnalysisResult
    created_at: datetime

    model_config = {"from_attributes": True}


class SubmissionDetailResponse(BaseModel):
    """Returned by GET /submissions/{id} and POST /submissions."""
    id: str
    title: str
    brief_text: str | None
    asset_urls: list[str]
    tier_requested: str
    status: str
    created_at: datetime
    analysis: AnalysisDetailResponse | None = None

    model_config = {"from_attributes": True}


class AuditResponse(BaseModel):
    """Returned by GET /submissions/{id}/audit. Just the processing_metadata."""
    submission_id: str
    analysis_id: str
    processing_metadata: dict | None

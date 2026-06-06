from __future__ import annotations

from pydantic import BaseModel, Field


class CreativeScore(BaseModel):
    dimension: str
    score: int = Field(ge=1, le=10)
    rationale: str


class AnalysisResult(BaseModel):
    summary: str
    scores: list[CreativeScore]
    strengths: list[str]
    improvements: list[str]
    target_audience: str
    tone: str
    recommendations: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    tier_used: str = ""
    model_used: str = ""

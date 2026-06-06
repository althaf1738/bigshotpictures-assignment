from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    title: Mapped[str] = mapped_column(Text)
    brief_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_urls: Mapped[list] = mapped_column(JSON, default=list)
    tier_requested: Mapped[str] = mapped_column(String(10), default="auto")
    content_type: Mapped[str] = mapped_column(String(32), default="auto")
    status: Mapped[str] = mapped_column(String(20), default="pending")

    analyses: Mapped[list[Analysis]] = relationship("Analysis", back_populates="submission", cascade="all, delete-orphan")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Queryable typed columns
    tier_used: Mapped[str] = mapped_column(String(10))
    model_used: Mapped[str] = mapped_column(String(128))
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    degradation_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    total_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_version: Mapped[str] = mapped_column(String(32), default="1.0")
    cost_estimate_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Full analysis output
    result: Mapped[dict] = mapped_column(JSON)

    # Audit trail: pool_attempts, token usage, degradation details
    processing_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    submission: Mapped[Submission] = relationship("Submission", back_populates="analyses")

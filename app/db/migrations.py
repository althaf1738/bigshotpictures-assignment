from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.observability.logging import get_logger

logger = get_logger(__name__)


async def run_migrations(engine: AsyncEngine) -> None:
    """Apply additive schema changes to an existing SQLite database."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(analyses)"))
        existing = {row[1] for row in result.fetchall()}

        # Rename duration_ms → total_latency_ms (SQLite 3.25+)
        if "duration_ms" in existing and "total_latency_ms" not in existing:
            await conn.execute(
                text("ALTER TABLE analyses RENAME COLUMN duration_ms TO total_latency_ms")
            )
            logger.info("migration_applied", change="rename duration_ms -> total_latency_ms")

        additions = [
            ("total_latency_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("degraded", "BOOLEAN NOT NULL DEFAULT 0"),
            ("degradation_reason", "TEXT"),
            ("processing_metadata", "JSON"),
        ]
        for col, col_def in additions:
            if col not in existing and col != "total_latency_ms":
                await conn.execute(
                    text(f"ALTER TABLE analyses ADD COLUMN {col} {col_def}")
                )
                logger.info("migration_applied", change=f"add column {col}")

        result = await conn.execute(text("PRAGMA table_info(submissions)"))
        sub_existing = {row[1] for row in result.fetchall()}
        if "content_type" not in sub_existing:
            await conn.execute(
                text("ALTER TABLE submissions ADD COLUMN content_type VARCHAR(32) NOT NULL DEFAULT 'auto'")
            )
            logger.info("migration_applied", change="add column content_type")

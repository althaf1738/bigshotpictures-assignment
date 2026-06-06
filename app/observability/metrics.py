from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis, Submission


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]


async def get_metrics(db: AsyncSession, uptime_seconds: int) -> dict:
    sub_total = await db.scalar(select(func.count()).select_from(Submission))
    status_rows = await db.execute(select(Submission.status, func.count()).group_by(Submission.status))
    by_status = dict(status_rows.all())

    analyses = (await db.execute(select(Analysis))).scalars().all()
    ana_total = len(analyses)
    degraded = sum(1 for a in analyses if a.degraded)
    degradation_rate = round(degraded / ana_total, 2) if ana_total else 0.0

    by_tier: Counter[str] = Counter()
    latencies: dict[str, list[int]] = defaultdict(list)
    degradation_reasons: Counter[str] = Counter()
    provider_calls: Counter[str] = Counter()
    provider_errors: Counter[str] = Counter()

    for analysis in analyses:
        by_tier[analysis.tier_used] += 1
        latencies[analysis.tier_used].append(analysis.total_latency_ms)
        if analysis.degraded and analysis.degradation_reason:
            degradation_reasons[analysis.degradation_reason] += 1

        for attempt in (analysis.processing_metadata or {}).get("pool_attempts", []):
            model = attempt.get("model")
            if not model:
                continue
            provider_calls[model] += 1
            if attempt.get("outcome") != "success":
                provider_errors[model] += 1

    latency_ms = {}
    for tier, values in latencies.items():
        latency_ms[f"{tier}_tier_p50"] = _percentile(values, 0.50)
        latency_ms[f"{tier}_tier_p95"] = _percentile(values, 0.95)

    providers = {
        model: {
            "calls": calls,
            "errors": provider_errors.get(model, 0),
            "error_rate": round(provider_errors.get(model, 0) / calls, 3) if calls else 0.0,
        }
        for model, calls in provider_calls.items()
    }

    return {
        "submissions": {
            "total": sub_total or 0,
            "by_tier": dict(by_tier),
            "by_status": by_status,
        },
        "analyses": {
            "total": ana_total,
            "degraded": degraded,
            "degradation_rate": degradation_rate,
            "degradation_reasons": dict(degradation_reasons),
        },
        "latency_ms": latency_ms,
        "providers": providers,
        "uptime_seconds": uptime_seconds,
    }

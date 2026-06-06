from __future__ import annotations

from app.repositories.analyses import AnalysisRepository
from app.repositories.submissions import SubmissionRepository
from app.schemas.analysis import AnalysisResult, CreativeScore
from app.schemas.submission import SubmissionCreate


def _make_result(summary: str = "Test summary") -> AnalysisResult:
    return AnalysisResult(
        summary=summary,
        scores=[CreativeScore(dimension="concept", score=8, rationale="Good")],
        strengths=["Strong"],
        improvements=["Improve"],
        target_audience="Everyone",
        tone="Neutral",
        recommendations=["Do this"],
        confidence=0.9,
        tier_used="fast",
        model_used="mock",
    )


async def test_create_and_get_submission(db_session):
    repo = SubmissionRepository(db_session)
    data = SubmissionCreate(title="My Ad", brief_text="Short brief", asset_urls=[], tier_override="auto")
    sub = await repo.create(data)
    assert sub.id
    assert sub.status == "pending"

    fetched = await repo.get(sub.id)
    assert fetched is not None
    assert fetched.title == "My Ad"


async def test_update_submission_status(db_session):
    repo = SubmissionRepository(db_session)
    sub = await repo.create(SubmissionCreate(title="T", brief_text=None, asset_urls=[], tier_override="fast"))
    await repo.update_status(sub.id, "done")
    fetched = await repo.get(sub.id)
    assert fetched.status == "done"


async def test_list_submissions(db_session):
    repo = SubmissionRepository(db_session)
    for i in range(3):
        await repo.create(SubmissionCreate(title=f"Sub {i}", brief_text=None, asset_urls=[], tier_override="fast"))
    subs = await repo.list(limit=10, offset=0)
    assert len(subs) >= 3


async def test_create_analysis(db_session):
    sub_repo = SubmissionRepository(db_session)
    ana_repo = AnalysisRepository(db_session)

    sub = await sub_repo.create(SubmissionCreate(title="Ad", brief_text=None, asset_urls=[], tier_override="fast"))
    result = _make_result()
    analysis = await ana_repo.create(
        submission_id=sub.id,
        tier_used="fast",
        model_used="mock",
        result=result,
        total_latency_ms=150,
    )
    assert analysis.id
    assert analysis.tier_used == "fast"

    by_sub = await ana_repo.get_by_submission(sub.id)
    assert len(by_sub) == 1
    assert by_sub[0].id == analysis.id


async def test_get_latest_analysis_by_submission(db_session):
    sub_repo = SubmissionRepository(db_session)
    ana_repo = AnalysisRepository(db_session)

    sub = await sub_repo.create(SubmissionCreate(title="Ad", brief_text=None, asset_urls=[], tier_override="fast"))
    first = await ana_repo.create(
        submission_id=sub.id,
        tier_used="fast",
        model_used="old-model",
        result=_make_result("Old summary"),
        total_latency_ms=100,
    )
    second = await ana_repo.create(
        submission_id=sub.id,
        tier_used="fast",
        model_used="new-model",
        result=_make_result("New summary"),
        total_latency_ms=200,
    )

    latest = await ana_repo.get_latest_by_submission(sub.id)
    assert latest is not None
    assert latest.id == second.id
    assert latest.id != first.id

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.observability.logging import correlation_id_var, get_logger
from app.repositories.analyses import AnalysisRepository
from app.repositories.submissions import SubmissionRepository
from app.routing.router import TierRouter
from app.schemas.analysis import AnalysisResult
from app.schemas.submission import SubmissionCreate
from app.tasks.analyze import run_analysis

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")
logger = get_logger(__name__)


def _waiting_sort_key(submission):
    is_waiting = submission.status in {"pending", "processing"}
    return (0 if is_waiting else 1, submission.created_at)


def _tpl(request, name, ctx=None):
    try:
        return templates.TemplateResponse(request=request, name=name, context=ctx or {})
    except TypeError:
        context = {"request": request, **(ctx or {})}
        return templates.TemplateResponse(name, context)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str = "",
    tier: str = "",
    sort: str = "newest",
    limit: int = 20,
    offset: int = 0,
):
    repo = SubmissionRepository(db)
    all_subs = await repo.list(limit=200, offset=0)

    if status:
        all_subs = [s for s in all_subs if s.status == status]
    if tier:
        all_subs = [s for s in all_subs if s.tier_requested == tier]

    if sort == "attention":
        order = {"failed": 0, "pending": 1, "processing": 2, "done": 3}
        all_subs.sort(key=lambda s: order.get(s.status, 9))
    elif sort == "waiting":
        all_subs.sort(key=_waiting_sort_key)

    total = len(all_subs)
    subs = all_subs[offset: offset + limit]

    return _tpl(request, "submission_list.html", {
        "submissions": subs,
        "total": total,
        "limit": limit,
        "offset": offset,
        "status_filter": status,
        "tier_filter": tier,
        "sort": sort,
    })


@router.get("/submit", response_class=HTMLResponse)
async def submit_form(request: Request):
    return _tpl(request, "submit.html")


@router.post("/submit")
async def submit_create(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    title = (form.get("title") or "").strip()
    brief_text = (form.get("brief_text") or "").strip() or None
    asset_urls_raw = (form.get("asset_urls_text") or "").strip()
    asset_urls = [u.strip() for u in asset_urls_raw.splitlines() if u.strip()]
    content_type = form.get("content_type") or "auto"

    # deep_analysis toggle: "on" → rich, absent → auto
    deep_analysis = form.get("deep_analysis") == "on"
    tier = "rich" if deep_analysis else "auto"

    if not title:
        return _tpl(request, "submit.html", {
            "flash": {"type": "error", "message": "Title is required."},
        })

    body = SubmissionCreate(
        title=title,
        brief_text=brief_text,
        asset_urls=asset_urls,
        tier_override=tier,
        content_type=content_type,
    )
    repo = SubmissionRepository(db)
    sub = await repo.create(body)

    strategy = request.app.state.strategy
    tier_router: TierRouter = request.app.state.tier_router
    background_tasks.add_task(run_analysis, sub.id, strategy, tier_router, str(uuid.uuid4()))

    return RedirectResponse(f"/dashboard/{sub.id}", status_code=303)


@router.get("/dashboard/{submission_id}", response_class=HTMLResponse)
async def submission_detail(
    submission_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = SubmissionRepository(db)
    sub = await repo.get(submission_id)
    if not sub:
        return RedirectResponse("/dashboard")
    return _tpl(request, "submission_detail.html", {"sub": sub})


@router.get("/dashboard/{submission_id}/status", response_class=HTMLResponse)
async def submission_status_poll(
    submission_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = SubmissionRepository(db)
    sub = await repo.get(submission_id)
    if not sub:
        return HTMLResponse('<div id="analysis-section"></div>')

    ana_repo = AnalysisRepository(db)
    latest_analysis = await ana_repo.get_latest_by_submission(submission_id)

    if sub.status == "done" and latest_analysis:
        result = AnalysisResult(**latest_analysis.result)
        return _tpl(request, "partials/analysis_result.html", {
            "result": result,
            "analysis": latest_analysis,
        })
    elif sub.status == "failed":
        return _tpl(request, "partials/analysis_failed.html")
    else:
        return _tpl(request, "partials/analysis_pending.html")


@router.post("/dashboard/{submission_id}/reanalyze")
async def reanalyze(
    submission_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    tier_override = request.query_params.get("tier")
    repo = SubmissionRepository(db)
    sub = await repo.get(submission_id)
    if not sub:
        return RedirectResponse("/dashboard", status_code=303)

    previous_tier_requested = sub.tier_requested
    previous_status = sub.status
    if tier_override:
        sub.tier_requested = tier_override
        await db.commit()

    await repo.update_status(submission_id, "pending")
    logger.info(
        "reanalyze_requested",
        submission_id=submission_id,
        previous_tier_requested=previous_tier_requested,
        new_tier_requested=sub.tier_requested,
        previous_status=previous_status,
        correlation_id=correlation_id_var.get(),
    )
    strategy = request.app.state.strategy
    tier_router: TierRouter = request.app.state.tier_router
    background_tasks.add_task(run_analysis, submission_id, strategy, tier_router, str(uuid.uuid4()))

    return RedirectResponse(f"/dashboard/{submission_id}", status_code=303)

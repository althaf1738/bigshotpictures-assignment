from __future__ import annotations


async def test_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_metrics(async_client):
    resp = await async_client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"submissions", "analyses", "latency_ms", "providers", "uptime_seconds"}
    assert set(data["submissions"].keys()) == {"total", "by_tier", "by_status"}
    assert set(data["analyses"].keys()) == {"total", "degraded", "degradation_rate", "degradation_reasons"}
    assert isinstance(data["uptime_seconds"], int)


async def test_create_fast_submission(async_client):
    resp = await async_client.post(
        "/submissions",
        json={"title": "Test Ad", "brief_text": "A quick product shot", "asset_urls": [], "tier_override": "fast"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["analysis"] is not None
    assert data["analysis"]["tier_used"] == "fast"
    assert "degraded" in data["analysis"]


async def test_create_rich_submission_returns_pending(async_client):
    resp = await async_client.post(
        "/submissions",
        json={"title": "Brand Campaign", "brief_text": "Strategic brand campaign", "asset_urls": [], "tier_override": "rich"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"


async def test_get_submission_detail(async_client):
    create_resp = await async_client.post(
        "/submissions",
        json={"title": "Lookup Test", "brief_text": "Simple brief", "asset_urls": [], "tier_override": "fast"},
    )
    sub_id = create_resp.json()["id"]

    get_resp = await async_client.get(f"/submissions/{sub_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == sub_id
    assert "brief_text" in data
    assert "asset_urls" in data


async def test_get_submission_audit(async_client):
    create_resp = await async_client.post(
        "/submissions",
        json={"title": "Audit Test", "brief_text": "Quick brief", "asset_urls": [], "tier_override": "fast"},
    )
    sub_id = create_resp.json()["id"]

    audit_resp = await async_client.get(f"/submissions/{sub_id}/audit")
    assert audit_resp.status_code == 200
    data = audit_resp.json()
    assert data["submission_id"] == sub_id
    assert "processing_metadata" in data


async def test_submission_detail_and_audit_use_latest_analysis(async_client):
    create_resp = await async_client.post(
        "/submissions",
        json={"title": "Latest Test", "brief_text": "Quick brief", "asset_urls": [], "tier_override": "fast"},
    )
    sub_id = create_resp.json()["id"]
    first_analysis_id = create_resp.json()["analysis"]["id"]

    reanalyze_resp = await async_client.post(f"/dashboard/{sub_id}/reanalyze?tier=fast")
    assert reanalyze_resp.status_code in (200, 303)

    detail_resp = await async_client.get(f"/submissions/{sub_id}")
    assert detail_resp.status_code == 200
    latest_analysis_id = detail_resp.json()["analysis"]["id"]
    assert latest_analysis_id != first_analysis_id

    audit_resp = await async_client.get(f"/submissions/{sub_id}/audit")
    assert audit_resp.status_code == 200
    assert audit_resp.json()["analysis_id"] == latest_analysis_id


async def test_get_nonexistent_submission(async_client):
    resp = await async_client.get("/submissions/nonexistent-id")
    assert resp.status_code == 404


async def test_list_submissions_summary_shape(async_client):
    for i in range(3):
        await async_client.post(
            "/submissions",
            json={"title": f"Ad {i}", "brief_text": "Quick brief", "asset_urls": [], "tier_override": "fast"},
        )
    resp = await async_client.get("/submissions?limit=10&offset=0")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) >= 3
    # Summary shape: no analysis, no brief_text
    for item in items:
        assert "analysis" not in item
        assert "brief_text" not in item
        assert "status" in item
        assert "tier_requested" in item


async def test_invalid_api_key_returns_401(async_client):
    resp = await async_client.get("/submissions", headers={"X-API-Key": "invalid"})
    assert resp.status_code == 401


async def test_missing_api_key_returns_422(async_client):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/submissions")
        assert resp.status_code in (401, 422)


async def test_correlation_id_in_response(async_client):
    resp = await async_client.get("/health")
    assert "x-correlation-id" in resp.headers or "X-Correlation-ID" in resp.headers

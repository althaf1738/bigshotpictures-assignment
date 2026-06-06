from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.routes.dashboard import _waiting_sort_key


def _submission(status: str, created_at: datetime):
    return SimpleNamespace(status=status, created_at=created_at)


def test_waiting_sort_prioritizes_oldest_active_submissions():
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    middle = datetime(2026, 1, 2, tzinfo=timezone.utc)
    new = datetime(2026, 1, 3, tzinfo=timezone.utc)

    submissions = [
        _submission("done", old),
        _submission("processing", new),
        _submission("failed", middle),
        _submission("pending", old),
        _submission("processing", middle),
    ]

    ordered = sorted(submissions, key=_waiting_sort_key)

    assert [s.status for s in ordered] == [
        "pending",
        "processing",
        "processing",
        "done",
        "failed",
    ]
    assert ordered[0].created_at == old
    assert ordered[1].created_at == middle
    assert ordered[2].created_at == new

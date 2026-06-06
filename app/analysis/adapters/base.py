from __future__ import annotations

from typing import Protocol

from app.analysis.pool import CallPayload


class ModelAdapter(Protocol):
    provider_prefix: str
    model_id: str

    async def call(self, payload: CallPayload) -> str:
        ...

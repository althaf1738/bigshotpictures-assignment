from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.analysis.adapters.base import ModelAdapter

AdapterFactory = Callable[[str, Any, bool], ModelAdapter]

_REGISTRY: dict[str, AdapterFactory] = {}


def register_adapter(prefix: str, factory: AdapterFactory) -> None:
    normalized = prefix.strip().lower()
    if not normalized:
        raise ValueError("Adapter prefix cannot be empty")

    existing = _REGISTRY.get(normalized)
    if existing is not None and existing is not factory:
        raise ValueError(f"Adapter already registered: {normalized}")
    _REGISTRY[normalized] = factory


def create_adapter(prefix: str, model_id: str, settings: Any, with_thinking: bool = False) -> ModelAdapter:
    normalized = prefix.strip().lower()
    try:
        factory = _REGISTRY[normalized]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise ValueError(f"Unknown provider prefix '{prefix}'. Known providers: {known}") from exc
    return factory(model_id, settings, with_thinking)


def registered_prefixes() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))

from __future__ import annotations


def load_adapters() -> None:
    """Import built-in adapters so their registry hooks run."""
    from app.analysis.adapters import anthropic  # noqa: F401
    from app.analysis.adapters import nim  # noqa: F401

from __future__ import annotations

"""Tests that adapter error translation is correct.

Uses mock httpx responses — no real API calls are made.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.analysis.adapters import load_adapters
from app.analysis.adapters.anthropic import AnthropicAdapter
from app.analysis.adapters.nim import NimAdapter
from app.analysis.adapters.registry import create_adapter, registered_prefixes, register_adapter
from app.analysis.exceptions import (
    AuthError, BadRequestError, ContentPolicyError,
    ProviderUnavailableError, RateLimitError,
)
from app.analysis.pool import CallPayload


def _payload():
    return CallPayload(system="sys", user_text="brief", stage="single")


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _Settings:
    anthropic_api_key = _Secret("anthropic-key")
    nim_api_key = _Secret("nim-key")
    nim_base_url = "http://localhost"
    provider_timeout_s = 5


def test_builtin_adapters_are_registered():
    load_adapters()
    assert {"anthropic", "nim"}.issubset(set(registered_prefixes()))


def test_create_adapter_uses_registered_factory():
    load_adapters()
    adapter = create_adapter("nim", "llama", _Settings())
    assert isinstance(adapter, NimAdapter)
    assert adapter.model_id == "llama"


def test_unknown_adapter_prefix_fails_clearly():
    load_adapters()
    with pytest.raises(ValueError, match="Unknown provider prefix 'missing'"):
        create_adapter("missing", "model", _Settings())


def test_register_adapter_rejects_duplicate_prefix():
    def factory(model_id, settings, with_thinking):
        return NimAdapter(model_id=model_id, api_key="k", base_url="http://localhost", timeout=5)

    with pytest.raises(ValueError, match="Adapter already registered: nim"):
        register_adapter("nim", factory)


# ---- NIM adapter ---- #

class TestNimAdapterErrorTranslation:
    def _adapter(self):
        return NimAdapter(model_id="llama", api_key="k", base_url="http://localhost", timeout=5)

    def _status_error(self, status_code: int):
        from openai import APIStatusError
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = {}
        resp.text = "error"
        return APIStatusError("err", response=resp, body={})

    async def test_429_raises_rate_limit(self):
        adapter = self._adapter()
        with patch.object(adapter._client.chat.completions, "create", side_effect=self._status_error(429)):
            with pytest.raises(RateLimitError):
                await adapter.call(_payload())

    async def test_401_raises_auth_error(self):
        adapter = self._adapter()
        with patch.object(adapter._client.chat.completions, "create", side_effect=self._status_error(401)):
            with pytest.raises(AuthError):
                await adapter.call(_payload())

    async def test_400_raises_bad_request(self):
        adapter = self._adapter()
        with patch.object(adapter._client.chat.completions, "create", side_effect=self._status_error(400)):
            with pytest.raises(BadRequestError):
                await adapter.call(_payload())

    async def test_503_raises_provider_unavailable(self):
        adapter = self._adapter()
        with patch.object(adapter._client.chat.completions, "create", side_effect=self._status_error(503)):
            with pytest.raises(ProviderUnavailableError):
                await adapter.call(_payload())

    async def test_timeout_raises_provider_unavailable(self):
        from openai import APITimeoutError
        adapter = self._adapter()
        with patch.object(adapter._client.chat.completions, "create", side_effect=APITimeoutError("timeout")):
            with pytest.raises(ProviderUnavailableError):
                await adapter.call(_payload())

    async def test_connection_error_raises_provider_unavailable(self):
        from openai import APIConnectionError
        import httpx
        adapter = self._adapter()
        err = APIConnectionError(request=httpx.Request("GET", "http://localhost"))
        with patch.object(adapter._client.chat.completions, "create", side_effect=err):
            with pytest.raises(ProviderUnavailableError):
                await adapter.call(_payload())


# ---- Anthropic adapter ---- #

class TestAnthropicAdapterErrorTranslation:
    def _adapter(self):
        return AnthropicAdapter(model_id="claude-haiku-4-5", api_key="k", timeout=5)

    def _status_error(self, status_code: int):
        from anthropic import APIStatusError
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = {}
        resp.text = "error"
        return APIStatusError("err", response=resp, body={})

    async def test_429_raises_rate_limit(self):
        adapter = self._adapter()
        with patch.object(adapter._client.messages, "create", side_effect=self._status_error(429)):
            with pytest.raises(RateLimitError):
                await adapter.call(_payload())

    async def test_401_raises_auth_error(self):
        adapter = self._adapter()
        with patch.object(adapter._client.messages, "create", side_effect=self._status_error(401)):
            with pytest.raises(AuthError):
                await adapter.call(_payload())

    async def test_400_raises_bad_request(self):
        adapter = self._adapter()
        with patch.object(adapter._client.messages, "create", side_effect=self._status_error(400)):
            with pytest.raises(BadRequestError):
                await adapter.call(_payload())

    async def test_503_raises_provider_unavailable(self):
        adapter = self._adapter()
        with patch.object(adapter._client.messages, "create", side_effect=self._status_error(503)):
            with pytest.raises(ProviderUnavailableError):
                await adapter.call(_payload())

    async def test_timeout_raises_provider_unavailable(self):
        from anthropic import APITimeoutError
        adapter = self._adapter()
        with patch.object(adapter._client.messages, "create", side_effect=APITimeoutError("timeout")):
            with pytest.raises(ProviderUnavailableError):
                await adapter.call(_payload())

from __future__ import annotations

import base64

import anthropic
from anthropic import APIConnectionError, APIStatusError, APITimeoutError

from app.analysis.adapters.registry import register_adapter
from app.analysis.exceptions import (
    AuthError, BadRequestError, ProviderUnavailableError, RateLimitError,
)
from app.analysis.pool import CallPayload


class AnthropicAdapter:
    provider_prefix = "anthropic"

    def __init__(
        self,
        model_id: str,
        api_key: str,
        timeout: float,
        with_thinking: bool = False,
    ) -> None:
        self.model_id = model_id
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout)
        self._with_thinking = with_thinking

    async def call(self, payload: CallPayload) -> str:
        messages = self._build_messages(payload)
        max_tokens = 2048 if payload.stage in ("stage_1", "stage_2") else 1024

        kwargs: dict = {
            "model": self.model_id,
            "max_tokens": max_tokens,
            "system": payload.system,
            "messages": messages,
        }
        if self._with_thinking and payload.stage == "stage_1":
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}

        try:
            response = await self._client.messages.create(**kwargs)
            return next((b.text for b in response.content if hasattr(b, "text")), "")
        except APITimeoutError as e:
            raise ProviderUnavailableError(str(e)) from e
        except APIConnectionError as e:
            raise ProviderUnavailableError(str(e)) from e
        except APIStatusError as e:
            raise self._translate_status(e) from e

    def _build_messages(self, payload: CallPayload) -> list[dict]:
        user_content: list[dict] = []
        for img in payload.images:
            b64 = base64.standard_b64encode(img).decode()
            user_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
        user_content.append({"type": "text", "text": payload.user_text or "(no brief provided)"})

        messages: list[dict] = [{"role": "user", "content": user_content}]
        if payload.stage == "stage_2" and payload.prior_assistant:
            messages.append({"role": "assistant", "content": payload.prior_assistant})
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": payload.follow_up_text or "Now perform the full evaluation."}],
            })
        return messages

    @staticmethod
    def _translate_status(e: APIStatusError) -> Exception:
        code = e.status_code
        if code == 429:
            return RateLimitError(str(e))
        if code in (401, 403):
            return AuthError(str(e))
        if code == 400:
            return BadRequestError(str(e))
        return ProviderUnavailableError(str(e))


def _factory(model_id: str, settings, with_thinking: bool) -> AnthropicAdapter:
    return AnthropicAdapter(
        model_id=model_id,
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.provider_timeout_s,
        with_thinking=with_thinking,
    )


register_adapter("anthropic", _factory)

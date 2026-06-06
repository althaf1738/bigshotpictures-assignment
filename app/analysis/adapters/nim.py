from __future__ import annotations

import base64

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

from app.analysis.adapters.registry import register_adapter
from app.analysis.exceptions import (
    AuthError, BadRequestError, ContentPolicyError,
    ProviderUnavailableError, RateLimitError,
)
from app.analysis.pool import CallPayload


class NimAdapter:
    provider_prefix = "nim"

    def __init__(self, model_id: str, api_key: str, base_url: str, timeout: float) -> None:
        self.model_id = model_id
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "no-key",
            timeout=timeout,
        )

    async def call(self, payload: CallPayload) -> str:
        messages = self._build_messages(payload)
        try:
            completion = await self._client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                temperature=0.2,
                max_tokens=2048,
            )
            return completion.choices[0].message.content or ""
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
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        user_content.append({"type": "text", "text": payload.user_text or "(no brief provided)"})

        messages: list[dict] = [
            {"role": "system", "content": payload.system},
            {"role": "user", "content": user_content},
        ]
        if payload.stage == "stage_2" and payload.prior_assistant:
            messages.append({"role": "assistant", "content": payload.prior_assistant})
            messages.append({
                "role": "user",
                "content": payload.follow_up_text or "Now perform the full evaluation.",
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
        if code == 422:
            return ContentPolicyError(str(e))
        return ProviderUnavailableError(str(e))


def _factory(model_id: str, settings, with_thinking: bool) -> NimAdapter:
    return NimAdapter(
        model_id=model_id,
        api_key=settings.nim_api_key.get_secret_value(),
        base_url=settings.nim_base_url,
        timeout=settings.provider_timeout_s,
    )


register_adapter("nim", _factory)

from __future__ import annotations

import base64

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

from app.analysis.adapters.registry import register_adapter
from app.analysis.exceptions import (
    AuthError, BadRequestError, ContentPolicyError,
    ProviderUnavailableError, RateLimitError,
)
from app.analysis.pool import CallPayload
from app.schemas.analysis import AnalysisResult


def _analysis_response_format() -> dict:
    schema = AnalysisResult.model_json_schema()
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "CreativeAnalysisResult",
            "schema": schema,
        },
    }


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
        uses_schema = payload.stage in ("single", "stage_2")
        kwargs = {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 4096 if uses_schema else 2048,
        }
        if uses_schema:
            kwargs["response_format"] = _analysis_response_format()

        try:
            completion = await self._client.chat.completions.create(**kwargs)
            text = self._message_text(completion)
            if text or not uses_schema:
                return text

            fallback_kwargs = dict(kwargs)
            fallback_kwargs.pop("response_format", None)
            fallback = await self._client.chat.completions.create(**fallback_kwargs)
            text = self._message_text(fallback)
            if text:
                return text

            raise ProviderUnavailableError("NIM returned empty message content")
        except APITimeoutError as e:
            raise ProviderUnavailableError(str(e)) from e
        except APIConnectionError as e:
            raise ProviderUnavailableError(str(e)) from e
        except APIStatusError as e:
            raise self._translate_status(e) from e

    @staticmethod
    def _message_text(completion) -> str:
        if not completion.choices:
            return ""

        message = completion.choices[0].message
        content = getattr(message, "content", "") or ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(part.get("text", ""))
                else:
                    parts.append(getattr(part, "text", ""))
            return "".join(parts)
        return str(content)

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

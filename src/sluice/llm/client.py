"""LLM 网关客户端——直接调 Anthropic Messages API（httpx），自实现 function calling。

重试策略：超时与 429/5xx 可重试，指数退避；4xx 客户端错误不重试直接抛。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..config.settings import Settings
from ..exceptions import LLMHTTPError, LLMTimeoutError
from .types import LLMResponse, Message, TextBlock, ToolDef, ToolUseBlock

_RETRYABLE_STATUS = {429, 500, 502, 503, 529}


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDef] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "max_tokens": 4096,
            "messages": [self._encode_message(m) for m in messages],
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [t.to_api() for t in tools]

        headers = {
            "x-api-key": self._settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        last_exc: Exception | None = None
        for attempt in range(self._settings.llm_max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self._settings.llm_base_url}/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=self._settings.llm_timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return self._parse_response(resp.json())
                err = LLMHTTPError(resp.status_code, resp.text)
                if resp.status_code in _RETRYABLE_STATUS:
                    last_exc = err
                else:
                    raise err
            if attempt < self._settings.llm_max_retries:
                time.sleep(2**attempt)

        if isinstance(last_exc, httpx.TimeoutException) or last_exc is None:
            raise LLMTimeoutError(
                f"LLM 请求超时（重试 {self._settings.llm_max_retries} 次后放弃）"
            ) from last_exc
        raise last_exc

    @staticmethod
    def _encode_message(m: Message) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            else:  # ToolResultBlock
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": b.content,
                        **({"is_error": True} if b.is_error else {}),
                    }
                )
        return {"role": m.role, "content": blocks}

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        content = []
        for b in data.get("content", []):
            if b.get("type") == "text":
                content.append(TextBlock(b["text"]))
            elif b.get("type") == "tool_use":
                content.append(ToolUseBlock(b["id"], b["name"], b.get("input", {})))
        return LLMResponse(
            content=content,
            stop_reason=data.get("stop_reason"),
            usage=data.get("usage", {}),
        )

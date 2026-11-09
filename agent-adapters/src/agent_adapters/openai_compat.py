"""OpenAI 兼容协议适配器 — infer 通道（spec §5.3 ModelAdapter 默认实现）。

把 InferRequest 映射为 chat.completions 调用；structured_output 走
response_format=json_schema。单测全 mock；真实网络只在显式配置时发生。
"""

from __future__ import annotations

import json

from agent_core.models import InferRequest, InferResponse, ModelCapabilities
from openai import AsyncOpenAI


class OpenAICompatAdapter:
    def __init__(self, *, base_url: str, api_key: str, capabilities: ModelCapabilities) -> None:
        self._caps = capabilities
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    def capabilities(self) -> ModelCapabilities:
        return self._caps

    async def infer(self, request: InferRequest) -> InferResponse:
        kwargs: dict = {
            "model": request.model_id,
            "messages": request.messages,
        }
        if request.tools:
            kwargs["tools"] = request.tools
        if request.structured_output:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": request.structured_output,
            }
        resp = await self._client.chat.completions.create(**kwargs)

        message = resp.choices[0].message
        tool_calls = []
        for tc in message.tool_calls or []:
            args = tc.function.arguments or "{}"
            tool_calls.append(
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(args) if isinstance(args, str) else args,
                }
            )
        usage = getattr(resp, "usage", None)
        return InferResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            usage={
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            },
        )

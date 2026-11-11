"""OpenAI 兼容协议适配器 — infer 通道（spec §5.3 ModelAdapter 默认实现）。

structured_output 降级链：json_schema → json_object（schema 注入提示）→ 裸提示。
供应商能力差异被隔离在适配器内，成功模式按实例缓存。
"""

from __future__ import annotations

import json

from agent_core.models import InferRequest, InferResponse, ModelCapabilities
from openai import AsyncOpenAI, BadRequestError

_FORMAT_JSON_SCHEMA = "json_schema"
_FORMAT_JSON_OBJECT = "json_object"
_FORMAT_NONE = "none"


class OpenAICompatAdapter:
    def __init__(self, *, base_url: str, api_key: str, capabilities: ModelCapabilities) -> None:
        self._caps = capabilities
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._format_mode: str | None = None  # None = 未探测

    def capabilities(self) -> ModelCapabilities:
        return self._caps

    @staticmethod
    def _schema_instruction(structured_output: dict) -> str:
        return (
            "\n[输出要求] 只输出一个 JSON 对象（不要 markdown 代码块、不要解释），"
            "必须符合此 JSON Schema:\n"
            + json.dumps(structured_output, ensure_ascii=False)
        )

    async def _create(self, kwargs: dict):
        return await self._client.chat.completions.create(**kwargs)

    async def infer(self, request: InferRequest) -> InferResponse:
        kwargs: dict = {
            "model": request.model_id,
            "messages": request.messages,
        }
        if request.tools:
            kwargs["tools"] = request.tools
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        if request.structured_output:
            resp = await self._infer_structured(request, kwargs)
        else:
            resp = await self._create(kwargs)

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

    async def _infer_structured(self, request: InferRequest, kwargs: dict):
        """结构化输出：按已探测模式直呼；未探测则 json_schema → json_object 降级。"""
        mode = self._format_mode or _FORMAT_JSON_SCHEMA
        try:
            return await self._create(self._structured_kwargs(kwargs, request, mode))
        except BadRequestError as e:
            if "response_format" not in str(e):
                raise
            fallback = {_FORMAT_JSON_SCHEMA: _FORMAT_JSON_OBJECT}.get(mode)
            if fallback is None:
                raise
            self._format_mode = fallback
            return await self._create(self._structured_kwargs(kwargs, request, fallback))

    def _structured_kwargs(self, kwargs: dict, request: InferRequest, mode: str) -> dict:
        kwargs = dict(kwargs)
        schema = request.structured_output
        if mode is _FORMAT_JSON_SCHEMA:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": schema}
            return kwargs
        # json_object：schema 以指令注入（模型可能输出不合式，由调用方校验-重试兜底）
        kwargs["response_format"] = {"type": "json_object"}
        last = str(request.messages[-1].get("content", ""))
        kwargs["messages"] = [
            *request.messages[:-1],
            {"role": "user", "content": last + self._schema_instruction(schema)},
        ]
        return kwargs

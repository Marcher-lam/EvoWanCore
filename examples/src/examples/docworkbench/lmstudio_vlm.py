"""LMStudioVLM — 本地 LM Studio 真 VLM 适配器（真 VLM 复跑，V2 §6.1 可选项）。

OpenAI 兼容 /v1/chat/completions；消息原样透传（image content part 由
ModelLoopPolicy 构造）；caps 声明 IMAGE 输入以命中视觉路由。
"""

from __future__ import annotations

import json

import httpx
from agent_core.models import InferRequest, InferResponse, Modality, ModelCapabilities

_BASE_URL = "http://localhost:1234/v1"


class LMStudioVLM:
    """ModelAdapter 实现：读图能力由 LM Studio 加载的视觉模型提供。"""

    def __init__(self, model_id: str, *, base_url: str = _BASE_URL) -> None:
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self.recorded_requests: list[InferRequest] = []
        self.calls = 0

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            model_id=self._model_id,
            inputs={Modality.TEXT, Modality.IMAGE},
            outputs={Modality.TEXT},
            interaction={"batch"},
            tools={"native_tool_call"},
        )

    async def infer(self, request: InferRequest) -> InferResponse:
        self.recorded_requests.append(request)
        self.calls += 1
        payload: dict = {
            "model": self._model_id,
            "messages": request.messages,
            "temperature": 0,
            "max_tokens": 4000,
            # 27b thinking 型：显式压 reasoning（实测 30-80s → 7.3s，读值仍正确）
            "reasoning_effort": "low",
        }
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = "auto"
        # 自愈：LM Studio 空闲驱逐/加载被取消时（"Model unloaded"/"Failed to load"）
        # 等待后重试（JIT 重载最多 3 次，间隔递增）——v4 曾因此类瞬态全崩
        import asyncio as _asyncio

        for attempt in range(3):
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions", json=payload
                )
                if resp.status_code == 400:
                    body = resp.text
                    if "unloaded" in body or "Failed to load" in body:
                        await _asyncio.sleep(15 * (attempt + 1))
                        continue
                resp.raise_for_status()
                data = resp.json()
                break
        else:
            resp.raise_for_status()
            data = resp.json()
        msg = data["choices"][0]["message"]
        tool_calls = [
            {
                "id": tc.get("id") or f"call_{i}",
                "name": tc["function"]["name"],
                "arguments": (
                    json.loads(tc["function"].get("arguments") or "{}")
                    if isinstance(tc["function"].get("arguments"), str)
                    else tc["function"].get("arguments") or {}
                ),
            }
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        return InferResponse(
            text=msg.get("content") or "",
            tool_calls=tool_calls,
            usage=data.get("usage", {}),
        )

"""OpenAI Realtime 接入点（V5 T11）——key 解析 + 会话骨架。

薄封装：key 缺失时给出明确可操作的错误（绝不硬编码、不静默降级）；
端点/模型默认指向 OpenAI Realtime API，其余委托既有 RealtimeSessionAdapter。
"""

from __future__ import annotations

import os

from agent_core.models import SessionConfig

from agent_adapters.realtime import RealtimeSessionAdapter

_DEFAULT_MODEL = "gpt-4o-realtime-preview"
_DEFAULT_ENDPOINT = f"wss://api.openai.com/v1/realtime?model={_DEFAULT_MODEL}"


class OpenAIRealtimeAdapter:
    """OpenAI Realtime 骨架：open_session / send_text / close_session。"""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY 未设置：请 export OPENAI_API_KEY=<your-key> "
                "或显式传 api_key=（key 不要写入被跟踪文件）"
            )
        self.model = model
        self._endpoint = endpoint or _DEFAULT_ENDPOINT
        self._api_key = key
        self._inner = RealtimeSessionAdapter(
            endpoint=self._endpoint, api_key=key
        )

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def open_session(
        self, *, instructions: str | None = None
    ) -> str:
        config = SessionConfig(
            model_id=self.model, instructions=instructions
        )
        return await self._inner.open_session(config)

    async def send_text(self, session_id: str, text: str) -> None:
        await self._inner.send_text(session_id, text)

    async def close_session(self, session_id: str) -> None:
        await self._inner.close_session(session_id)

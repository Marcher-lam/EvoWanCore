"""A2A（Agent 间通信）——HTTP JSON 信封协议 + 注册表（V5 T6-T7）。

实现 reserved.py 的 AgentTransport Protocol：
- HTTPAgentTransport：send POST 信封到远端 /a2a；receive 从本地队列弹出
- InboundA2AHandler：轻量 aiohttp 接收端（可选，接收方用）
- A2AEnvelope：Pydantic 信封模型
- AgentRegistry：Agent 注册/发现（name + capabilities + endpoint）
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp
from aiohttp import web
from pydantic import BaseModel, Field


class A2AEnvelope(BaseModel):
    """A2A 信封：Agent 间消息的标准封装。"""

    message_id: str = Field(default_factory=lambda: f"a2a_{uuid.uuid4().hex[:12]}")
    from_agent: str
    to_agent: str
    message_type: str  # task_request / task_result / query / reply / error
    payload: dict = {}
    reply_to: str | None = None
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class HTTPAgentTransport:
    """实现 AgentTransport Protocol：HTTP POST 信封到远端 Agent。"""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._inbox: deque[A2AEnvelope] = deque()

    async def send(self, envelope: dict | A2AEnvelope) -> A2AEnvelope | None:
        """POST 信封到远端 /a2a；远端可选返回回复信封。"""
        env = (
            envelope
            if isinstance(envelope, A2AEnvelope)
            else A2AEnvelope(**envelope)
        )
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self._base_url}/a2a",
                json=env.model_dump(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 204:
                    return None  # 无回复（fire-and-forget）
                resp.raise_for_status()
                data = await resp.json()
                reply = A2AEnvelope(**data)
                self._inbox.append(reply)
                return reply

    async def receive(self) -> dict:
        """从本地收件箱弹出最新信封（阻塞直到非空）。"""
        while not self._inbox:
            import asyncio

            await asyncio.sleep(0.01)
        env = self._inbox.popleft()
        return env.model_dump()


class InboundA2AHandler:
    """轻量 aiohttp 接收端：接收信封 → handler 回调 → 可选回复。"""

    def __init__(
        self,
        port: int,
        handler: Callable[[A2AEnvelope], Any],
        host: str = "127.0.0.1",
    ) -> None:
        self._port = port
        self._host = host
        self._handler = handler
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        async def _a2a(request: web.Request) -> web.Response:
            envelope = A2AEnvelope(**await request.json())
            reply = self._handler(envelope)
            if hasattr(reply, "__await__"):
                reply = await reply
            if reply is None:
                return web.Response(status=204)
            return web.json_response(reply.model_dump())

        app = web.Application()
        app.router.add_post("/a2a", _a2a)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()


class AgentRegistry:
    """进程内 Agent 注册表：注册/发现/注销。"""

    def __init__(self) -> None:
        self._agents: dict[str, dict] = {}

    def register(self, name: str, capabilities: list[str], endpoint: str) -> None:
        self._agents[name] = {
            "name": name, "capabilities": capabilities, "endpoint": endpoint,
        }

    def unregister(self, name: str) -> None:
        self._agents.pop(name, None)

    def get(self, name: str) -> dict | None:
        return self._agents.get(name)

    def discover(self, capability: str) -> list[dict]:
        return [
            a for a in self._agents.values()
            if capability in a.get("capabilities", [])
        ]

    def all(self) -> list[dict]:
        return list(self._agents.values())

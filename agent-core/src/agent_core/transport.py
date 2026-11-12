"""A2A（Agent 间通信）——HTTP JSON 信封协议 + 注册表（V5 T6-T7）。

实现 reserved.py 的 AgentTransport Protocol：
- HTTPAgentTransport：send POST 信封到远端 /a2a（回复入收件箱）；receive 取回复
- InboundA2AHandler：轻量 aiohttp 接收端（可选，接收方用）
- A2AEnvelope：Pydantic 信封模型
- AgentRegistry：Agent 注册/发现（name + capabilities + endpoint）
"""

from __future__ import annotations

import asyncio
import copy
import inspect
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
    """实现 AgentTransport Protocol：send POST 信封；receive 从收件箱取回复。"""

    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._inbox: deque[A2AEnvelope] = deque(maxlen=1024)
        self._inbox_event = asyncio.Event()

    async def send(self, envelope: dict | A2AEnvelope) -> None:
        """POST 信封到远端 /a2a；远端回复（若有）入本地收件箱（receive 取）。"""
        env = (
            envelope
            if isinstance(envelope, A2AEnvelope)
            else A2AEnvelope(**envelope)
        )
        headers = (
            {"Authorization": f"Bearer {self._auth_token}"}
            if self._auth_token else {}
        )
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self._base_url}/a2a",
                json=env.model_dump(),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 204:
                    return  # 无回复（fire-and-forget）
                resp.raise_for_status()
                self._inbox.append(A2AEnvelope(**await resp.json()))
                self._inbox_event.set()

    async def receive(self) -> dict:
        """阻塞直到收件箱非空，弹出最早到达的回复（协作等待，无自旋）。"""
        while not self._inbox:
            self._inbox_event.clear()
            if self._inbox:  # double-check：clear 与 append 竞态
                break
            await self._inbox_event.wait()
        return self._inbox.popleft().model_dump()


class InboundA2AHandler:
    """轻量 aiohttp 接收端：接收信封 → handler 回调 → 可选回复。

    handler 返回 A2AEnvelope（作为回复）或 None（204 无回复）；
    回调抛异常 → 500（不吞错）。
    鉴权（V7-T4）：auth_token 设置后，POST /a2a 必须带
    `Authorization: Bearer <token>`，否则 401；默认 None=开放（回环部署兼容）。
    """

    def __init__(
        self,
        port: int,
        handler: Callable[[A2AEnvelope], Any],
        host: str = "127.0.0.1",
        auth_token: str | None = None,
    ) -> None:
        self._port = port
        self._host = host
        self._handler = handler
        self._auth_token = auth_token
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        async def _a2a(request: web.Request) -> web.Response:
            if self._auth_token is not None:
                auth = request.headers.get("Authorization", "")
                if auth != f"Bearer {self._auth_token}":
                    return web.json_response(
                        {"error": "unauthorized"}, status=401
                    )
            try:
                envelope = A2AEnvelope(**await request.json())
                reply = self._handler(envelope)
                if inspect.isawaitable(reply):
                    reply = await reply
            except Exception as e:  # noqa: BLE001 — 边界如实回传
                return web.json_response(
                    {"error": f"{type(e).__name__}: {e}"}, status=500
                )
            if reply is None:
                return web.Response(status=204)
            if isinstance(reply, dict):
                reply = A2AEnvelope(**reply)
            if not isinstance(reply, A2AEnvelope):
                return web.json_response(
                    {"error": "handler 必须返回 A2AEnvelope/dict/None"}, status=500
                )
            return web.json_response(reply.model_dump())

        app = web.Application()
        app.router.add_post("/a2a", _a2a)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        # V7-T3：port=0 → 回读实际端口
        if self._port == 0:
            addresses = self._runner.addresses
            self._port = addresses[0][1] if addresses else self._port

    @property
    def port(self) -> int:
        """实际监听端口（port=0 启动后为内核分配值）。"""
        return self._port

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()


class AgentRegistry:
    """进程内 Agent 注册表：注册/发现/注销。返回副本（外部不可变性穿透）。"""

    def __init__(self) -> None:
        self._agents: dict[str, dict] = {}

    def register(self, name: str, capabilities: list[str], endpoint: str) -> None:
        self._agents[name] = {
            "name": name, "capabilities": list(capabilities), "endpoint": endpoint,
        }

    def unregister(self, name: str) -> None:
        self._agents.pop(name, None)

    def get(self, name: str) -> dict | None:
        rec = self._agents.get(name)
        return copy.deepcopy(rec) if rec else None

    def discover(self, capability: str) -> list[dict]:
        return copy.deepcopy([
            a for a in self._agents.values()
            if capability in a.get("capabilities", [])
        ])

    def all(self) -> list[dict]:
        return copy.deepcopy(list(self._agents.values()))

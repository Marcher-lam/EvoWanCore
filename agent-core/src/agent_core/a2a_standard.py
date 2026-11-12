"""A2A Google spec 核心子集（V8-T1，终评审修复版）——Agent Card + JSON-RPC message/send。

范围 Ruling（计划文档）：只做 Card 发布 + message/send 收发；
streaming（SSE）/push notifications/tasks 生命周期/artifacts 不做。
wire format 对齐 A2A spec 0.2：**camelCase**（alias_generator），
Message 携带 `kind: "message"` 判别字段，Card 含 `preferredTransport`。
与内部 A2AEnvelope 通过 text part 桥接：
params.message.parts[].text → envelope.payload.text。
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable
from typing import Any

import aiohttp
from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from agent_core.transport import A2AEnvelope

_A2A_PROTOCOL_VERSION = "0.2.0"


class _CamelModel(BaseModel):
    """A2A wire 模型基类：字段 camelCase 序列化，构造时接受 snake/camel。"""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True
    )


class AgentCardSkill(_CamelModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = []


class AgentCardCapabilities(_CamelModel):
    streaming: bool = False  # Ruling: 不做 streaming
    push_notifications: bool = False


class AgentCard(_CamelModel):
    """A2A Agent Card（核心字段子集，wire=camelCase）。"""

    name: str
    description: str = ""
    url: str
    version: str = "1.0.0"
    protocol_version: str = _A2A_PROTOCOL_VERSION
    preferred_transport: str = "JSONRPC"
    capabilities: AgentCardCapabilities = Field(
        default_factory=AgentCardCapabilities
    )
    skills: list[AgentCardSkill] = []

    def well_known_path(self) -> str:
        return "/.well-known/agent-card.json"


class A2ARpcError(RuntimeError):
    """JSON-RPC error 的结构化透出（code/message/id 不折进字符串）。"""

    def __init__(self, code: int, message: str, req_id: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.rpc_message = message
        self.req_id = req_id


def _jsonrpc_result(req_id: Any, result: dict) -> web.Response:
    return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_error(req_id: Any, code: int, message: str) -> web.Response:
    return web.json_response({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": code, "message": message},
    })


def _extract_text(params: dict) -> str:
    """从 message.parts[] 拼接 text part（A2A Message → 内部信封桥）。"""
    message = params.get("message") or {}
    parts = message.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
    return "\n".join(t for t in texts if t)


def _bearer_ok(auth_header: str, expected: str) -> bool:
    try:
        return hmac.compare_digest(auth_header.encode("latin-1"),
                                   expected.encode("latin-1"))
    except (UnicodeEncodeError, ValueError):
        return False  # 非 latin-1 头（aiohttp 解码边界）→ 拒绝


class A2AStandardEndpoint:
    """A2A 标准端点：Card 发布 + JSON-RPC message/send（aiohttp）。"""

    def __init__(
        self,
        card: AgentCard,
        handler: Callable[[A2AEnvelope], Any],
        port: int = 0,
        host: str = "127.0.0.1",
        auth_token: str | None = None,
    ) -> None:
        self._card = card
        self._handler = handler
        self._port = port
        self._host = host
        self._auth_token = auth_token
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        async def _card_route(_request: web.Request) -> web.Response:
            # wire=camelCase（第三方 A2A 客户端互操作面）
            return web.json_response(self._card.model_dump(by_alias=True))

        async def _rpc(request: web.Request) -> web.Response:
            if self._auth_token is not None:
                auth = request.headers.get("Authorization", "")
                if not _bearer_ok(auth, f"Bearer {self._auth_token}"):
                    return web.json_response(
                        {"error": "unauthorized"}, status=401,
                        headers={"WWW-Authenticate": "Bearer"},
                    )
            try:
                rpc = await request.json()
            except (ValueError, aiohttp.ContentTypeError):
                return _jsonrpc_error(None, -32700, "Parse error")
            if not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0":
                return _jsonrpc_error(None, -32600, "Invalid Request")
            req_id = rpc.get("id")
            if "method" not in rpc:
                return _jsonrpc_error(req_id, -32600, "Invalid Request")
            if rpc["method"] != "message/send":
                return _jsonrpc_error(
                    req_id, -32601, f"Method not found: {rpc['method']}"
                )

            params = rpc.get("params") or {}
            message = params.get("message")
            if not isinstance(message, dict) or not message.get("parts"):
                return _jsonrpc_error(
                    req_id, -32602, "Invalid params: message.parts 必填"
                )
            text = _extract_text(params)
            envelope = A2AEnvelope(
                # 稳定远端身份（逐消息 messageId 会让 ACL/路由漂移）；
                # 远端 messageId 存 payload 供对账
                from_agent="a2a:remote",
                to_agent=self._card.name,
                message_type="task_request",
                payload={"text": text, "message_id": message.get("messageId", "")},
            )
            reply = self._handler(envelope)
            if hasattr(reply, "__await__"):
                reply = await reply
            reply_text = (
                reply.payload.get("text", "") if reply is not None else ""
            )
            # 响应 = A2A Message（kind 判别字段 + role=agent + text part）
            return _jsonrpc_result(req_id, {
                "kind": "message",
                "messageId": f"msg_{uuid.uuid4().hex[:12]}",
                "role": "agent",
                "parts": [{"kind": "text", "text": reply_text}],
            })

        app = web.Application()
        app.router.add_get(self._card.well_known_path(), _card_route)
        app.router.add_post("/", _rpc)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        if self._port == 0:
            addresses = self._runner.addresses
            if not addresses:
                raise RuntimeError("A2A endpoint 未监听任何地址——端口回读失败")
            self._port = addresses[0][1]
        # Card.url 回写实际监听地址（发现的 Card 不得指向假 URL）
        self._card = self._card.model_copy(update={
            "url": f"http://{self._host}:{self._port}/"
        })

    @property
    def port(self) -> int:
        return self._port

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()


class A2AStandardClient:
    """A2A 标准客户端：send_text → JSON-RPC message/send → agent 回复文本。"""

    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token

    async def send_text(self, text: str) -> str:
        headers = (
            {"Authorization": f"Bearer {self._auth_token}"}
            if self._auth_token else {}
        )
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "messageId": f"msg_{uuid.uuid4().hex[:12]}",
                    "role": "user",
                    "parts": [{"kind": "text", "text": text}],
                }
            },
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self._base_url}/", json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                body = await resp.json()
        if "error" in body:
            raise A2ARpcError(
                body["error"].get("code", -32603),
                body["error"].get("message", ""),
                body.get("id"),
            )
        parts = body.get("result", {}).get("parts", [])
        return "\n".join(
            p.get("text", "") for p in parts if p.get("kind") == "text"
        )

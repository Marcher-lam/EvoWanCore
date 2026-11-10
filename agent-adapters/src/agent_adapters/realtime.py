"""RealtimeSessionAdapter — 供应商 Realtime WebSocket 客户端（T24，spec §5.3 session）。

事件流（含打断）、有界队列背压（满时丢最旧 audio_out）、断线自动重连一次
并 resume 会话。供应商协议差异被隔离在本适配器内。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import websockets
from agent_core.models import (
    InferRequest,
    InferResponse,
    ModelCapabilities,
    SessionConfig,
    SessionEvent,
)

_QUEUE_MAX = 64
_EVENT_MAP = {
    "transcript_in": "transcript_in",
    "transcript_out": "transcript_out",
    "tool_call": "tool_call",
    "audio_out": "audio_out",
    "interrupted": "interrupted",
    "error": "error",
    "closed": "closed",
    "session_resumed": "session_resumed",
    "opened": "opened",
}


class _Session:
    def __init__(self, config: SessionConfig, queue: asyncio.Queue):
        self.config = config
        self.queue = queue
        self.ws = None
        self.resumed = False
        self.closing = False


class RealtimeSessionAdapter:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._caps = capabilities or ModelCapabilities(
            model_id="realtime", interaction={"realtime", "streaming"}
        )
        self._sessions: dict[str, _Session] = {}

    def capabilities(self) -> ModelCapabilities:
        return self._caps

    async def infer(self, request: InferRequest) -> InferResponse:  # pragma: no cover
        raise NotImplementedError("Realtime 适配器不走 infer 通道")

    async def _connect(self, session: _Session) -> None:
        session.ws = await websockets.connect(
            self._endpoint,
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
            open_timeout=10,
        )
        await session.ws.send(
            json.dumps(
                {"type": "session.update", "instructions": session.config.instructions or ""}
            )
        )

    async def open_session(self, config: SessionConfig) -> str:
        session_id = f"sess_{uuid.uuid4().hex[:10]}"
        session = _Session(config, asyncio.Queue(maxsize=_QUEUE_MAX))
        self._sessions[session_id] = session
        await self._connect(session)
        asyncio.create_task(self._read_loop(session_id))
        return session_id

    async def _read_loop(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        reconnected = False
        while True:
            try:
                raw = await session.ws.recv()  # 显式 recv：关闭必然抛错，绝不空转
            except websockets.ConnectionClosed:
                if session.closing:
                    return
                if reconnected:
                    self._enqueue(
                        session,
                        SessionEvent(type="error", payload={"reason": "connection lost twice"}),
                    )
                    return
                reconnected = True
                session.resumed = True
                try:
                    await self._connect(session)
                    await session.ws.send(json.dumps({"type": "resume", "session_id": session_id}))
                    continue
                except BaseException as e:  # noqa: BLE001 — 错误必须成为事件，不许任务静默死亡
                    self._enqueue(
                        session,
                        SessionEvent(
                            type="error",
                            payload={"reason": f"reconnect failed: {type(e).__name__}: {e}"},
                        ),
                    )
                    return
            except BaseException as e:  # noqa: BLE001
                self._enqueue(
                    session,
                    SessionEvent(type="error", payload={"reason": f"{type(e).__name__}: {e}"}),
                )
                return

            try:
                msg = json.loads(raw)
                etype = _EVENT_MAP.get(msg.get("type", ""), "error")
                self._enqueue(session, SessionEvent(type=etype, payload=msg.get("payload", {})))
            except BaseException as e:  # noqa: BLE001 — 单条消息畸形不许杀掉会话
                self._enqueue(
                    session,
                    SessionEvent(type="error", payload={"reason": f"bad message: {e}"}),
                )
                continue
            if etype == "closed":
                return

    def _enqueue(self, session: _Session, event: SessionEvent) -> None:
        """有界队列背压：满时优先丢最旧的 audio_out 分片。"""
        if session.queue.full():
            dropped = False
            for _i in range(session.queue.qsize()):
                try:
                    candidate = session.queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    break
                if candidate.type == "audio_out" and not dropped:
                    dropped = True  # 丢弃，不入回
                    continue
                session.queue.put_nowait(candidate)
        try:
            session.queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover
            pass

    async def stream(self, session_id: str):
        session = self._sessions[session_id]
        while True:
            event = await session.queue.get()
            yield event

    async def send_json(self, session_id: str, message: dict) -> None:
        await self._sessions[session_id].ws.send(json.dumps(message))

    async def send_text(self, session_id: str, text: str) -> None:
        await self.send_json(session_id, {"type": "text", "text": text})

    async def send_audio(self, session_id: str, chunk: bytes) -> None:
        await self._sessions[session_id].ws.send(
            json.dumps({"type": "audio", "len": len(chunk)})
        )

    async def interrupt(self, session_id: str) -> None:
        await self._sessions[session_id].ws.send(json.dumps({"type": "interrupt"}))

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.closing = True
        try:
            await session.ws.send(json.dumps({"type": "close"}))
            await session.ws.close()
        except (websockets.ConnectionClosed, OSError):
            pass

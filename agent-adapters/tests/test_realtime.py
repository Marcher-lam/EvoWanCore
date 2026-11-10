"""T24 Realtime 会话适配器：打断传播、断线重连、工具调用走已发布技能。"""

import asyncio
import json

from agent_adapters.realtime import RealtimeSessionAdapter
from agent_core.models import SessionConfig, SessionEvent
from websockets.asyncio.server import serve


async def _start_server(handler, port: int = 0):
    server = await serve(handler, "127.0.0.1", port)
    actual_port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{actual_port}/v1/realtime"


async def _collect(
    adapter: RealtimeSessionAdapter, session_id: str, stop_types: set[str], limit: int = 20
):
    events = []
    async for ev in adapter.stream(session_id):
        events.append(ev)
        if ev.type in stop_types or len(events) >= limit:
            break
    return events


async def test_interrupt_event_propagates() -> None:
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            received.append(msg)
            if msg["type"] == "interrupt":
                await ws.send(json.dumps({"type": "interrupted", "payload": {"at_ms": 99}}))
            if msg["type"] == "close":
                await ws.close()

    server, url = await _start_server(handler)
    try:
        adapter = RealtimeSessionAdapter(endpoint=url, api_key="sk-test")
        session_id = await adapter.open_session(
            SessionConfig(model_id="rt-x", instructions="你是文档助手")
        )
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: asyncio.ensure_future(adapter.interrupt(session_id)))
        events = await _collect(adapter, session_id, stop_types={"interrupted"})
        assert any(e.type == "interrupted" and e.payload.get("at_ms") == 99 for e in events)
        assert any(m["type"] == "interrupt" for m in received)
        await adapter.close_session(session_id)
    finally:
        server.close()
        await server.wait_closed()


async def test_reconnect_resumes_without_losing_session_state() -> None:
    instructions_seen: list[str] = []
    connections = {"n": 0}

    async def handler(ws):
        connections["n"] += 1
        if connections["n"] == 1:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "session.update":
                    instructions_seen.append(msg.get("instructions", ""))
                    await ws.send(
                        json.dumps({"type": "transcript_out", "payload": {"text": "你好"}})
                    )
                    await asyncio.sleep(0.05)
                    await ws.close()  # 异常断开
                    return
        else:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "session.update":
                    instructions_seen.append(msg.get("instructions", ""))
                if msg["type"] == "resume":
                    resumed = {
                        "type": "session_resumed",
                        "payload": {"session_id": msg["session_id"]},
                    }
                    await ws.send(json.dumps(resumed))
                    await ws.send(
                        json.dumps({"type": "transcript_out", "payload": {"text": "需要导出吗"}})
                    )
                    await ws.send(json.dumps({"type": "audio_out", "payload": {"chunk": 1}}))
                if msg["type"] == "close":
                    await ws.close()

    server, url = await _start_server(handler)
    try:
        adapter = RealtimeSessionAdapter(endpoint=url, api_key="sk-test")
        session_id = await adapter.open_session(
            SessionConfig(
                model_id="rt-x",
                instructions="你是文档助手；已知规则：convert_format(dpi=200)",
            )
        )
        events = await _collect(adapter, session_id, stop_types={"audio_out"}, limit=10)
        types = [e.type for e in events]
        # 断线重连后：transcript 连续（两条都收到）、会话恢复事件、指令完整传递
        assert "transcript_out" in types
        texts = [e.payload.get("text") for e in events if e.type == "transcript_out"]
        assert "你好" in texts and "需要导出吗" in texts
        assert "session_resumed" in types
        assert connections["n"] == 2  # 重连了一次
        assert all("convert_format" in i for i in instructions_seen)  # 技能/记忆引用完整
        await adapter.close_session(session_id)
    finally:
        server.close()
        await server.wait_closed()


async def test_send_text_and_audio() -> None:
    got: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            got.append(msg)
            if msg["type"] in ("text", "audio"):
                await ws.send(
                    json.dumps({"type": "transcript_in", "payload": {"echo": msg["type"]}})
                )
            if msg["type"] == "close":
                await ws.close()

    server, url = await _start_server(handler)
    try:
        adapter = RealtimeSessionAdapter(endpoint=url, api_key="k")
        sid = await adapter.open_session(SessionConfig(model_id="rt-x"))
        await adapter.send_text(sid, "导出这份文档")
        await adapter.send_audio(sid, b"\x00\x01")
        events = await _collect(adapter, sid, stop_types={"transcript_in"})
        assert len([e for e in events if e.type == "transcript_in"]) >= 1
        await adapter.close_session(sid)
    finally:
        server.close()
        await server.wait_closed()


async def test_stream_events_are_session_event_models() -> None:
    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "session.update":
                await ws.send(json.dumps({"type": "opened", "payload": {}}))
                await ws.close()

    server, url = await _start_server(handler)
    try:
        adapter = RealtimeSessionAdapter(endpoint=url, api_key="k")
        sid = await adapter.open_session(SessionConfig(model_id="rt-x"))
        events = await _collect(adapter, sid, stop_types=set(), limit=1)
        assert isinstance(events[0], SessionEvent)
        await adapter.close_session(sid)
    finally:
        server.close()
        await server.wait_closed()

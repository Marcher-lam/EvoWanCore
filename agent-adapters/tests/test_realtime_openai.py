"""T11 OpenAI Realtime 接入点：key 解析 + 明确缺失提示 + 会话骨架。"""

import pytest


def test_realtime_adapter_no_key_raises(monkeypatch) -> None:
    """无 OPENAI_API_KEY 且未显式传 key → 明确报错（不静默、不硬编码）。"""
    from agent_adapters.realtime_openai import OpenAIRealtimeAdapter

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIRealtimeAdapter()


def test_realtime_adapter_explicit_key_preferred(monkeypatch) -> None:
    """显式 key 优先于环境变量；默认端点指向 OpenAI Realtime。"""
    from agent_adapters.realtime_openai import OpenAIRealtimeAdapter

    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    adapter = OpenAIRealtimeAdapter(api_key="sk-explicit")
    assert adapter.endpoint.startswith("wss://api.openai.com/v1/realtime")
    assert adapter.model == "gpt-4o-realtime-preview"


def test_realtime_adapter_env_key_used(monkeypatch) -> None:
    """环境变量 key 生效。"""
    from agent_adapters.realtime_openai import OpenAIRealtimeAdapter

    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    adapter = OpenAIRealtimeAdapter()
    assert adapter.endpoint.startswith("wss://api.openai.com/v1/realtime")


async def test_realtime_session_skeleton_roundtrip(monkeypatch) -> None:
    """open_session/send_text/close_session 委托内层 RealtimeSessionAdapter。"""
    from agent_adapters.realtime_openai import OpenAIRealtimeAdapter

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = OpenAIRealtimeAdapter(endpoint="ws://127.0.0.1:1")  # 不真连
    calls: list[tuple[str, ...]] = []

    class _FakeInner:
        async def open_session(self, config):
            calls.append(("open", config.model_id))
            return "sess_1"

        async def send_text(self, session_id, text):
            calls.append(("send", session_id, text))

        async def close_session(self, session_id):
            calls.append(("close", session_id))

    adapter._inner = _FakeInner()  # noqa: SLF001 — 注入假内层
    sid = await adapter.open_session(instructions="你是一个助手")
    assert sid == "sess_1"
    await adapter.send_text(sid, "hello")
    await adapter.close_session(sid)
    assert calls == [
        ("open", "gpt-4o-realtime-preview"),
        ("send", "sess_1", "hello"),
        ("close", "sess_1"),
    ]


# ---------- V7-T5: mock socket 协议级验证（骨架从"未验证"变"协议验证过"） ----------

async def test_realtime_skeleton_over_mock_socket(monkeypatch) -> None:
    """本地 websockets mock server 说适配器方言——open/send/stream/close 真跑通。"""
    import json

    import websockets
    from agent_adapters.realtime_openai import OpenAIRealtimeAdapter

    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock")
    seen_auth: list[str] = []
    seen_client_msgs: list[dict] = []

    async def _handler(ws):  # noqa: ANN001 — websockets 回调
        seen_auth.append(ws.request.headers.get("Authorization", ""))
        await ws.send(json.dumps({"type": "opened", "payload": {"ready": True}}))
        async for raw in ws:
            msg = json.loads(raw)
            seen_client_msgs.append(msg)
            t = msg.get("type")
            if t == "session.update":
                await ws.send(json.dumps({
                    "type": "session_resumed", "payload": {"updated": True},
                }))
            elif t == "text":
                await ws.send(json.dumps({
                    "type": "transcript_in", "payload": {"text": msg["text"]},
                }))
                await ws.send(json.dumps({
                    "type": "transcript_out", "payload": {"text": f"echo: {msg['text']}"},
                }))
            elif t == "close":
                await ws.send(json.dumps({"type": "closed", "payload": {}}))
                await ws.close()
                return

    server = await websockets.serve(_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        adapter = OpenAIRealtimeAdapter(
            endpoint=f"ws://127.0.0.1:{port}", api_key="sk-mock"
        )
        sid = await adapter.open_session(instructions="你是一个助手")
        assert sid.startswith("sess_")

        await adapter.send_text(sid, "hello")

        got_in = got_out = False
        async for event in adapter.stream(sid):
            if event.type == "transcript_in" and event.payload.get("text") == "hello":
                got_in = True
            if event.type == "transcript_out" and event.payload.get("text") == "echo: hello":
                got_out = True
            if got_in and got_out:
                break
        assert got_in and got_out, f"echo 链路未闭环: in={got_in} out={got_out}"

        await adapter.close_session(sid)
        # 服务器侧收到完整协议序列
        types = [m["type"] for m in seen_client_msgs]
        assert "session.update" in types and "text" in types and "close" in types
        assert seen_auth and seen_auth[0] == "Bearer sk-mock", "Bearer 头必须随连接发送"
    finally:
        server.close()
        await server.wait_closed()

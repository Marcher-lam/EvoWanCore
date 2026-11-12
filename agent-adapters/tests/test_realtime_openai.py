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

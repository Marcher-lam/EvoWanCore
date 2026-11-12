"""T6/T7 A2A：HTTP JSON 信封 AgentTransport + 注册表。"""

import asyncio
from pathlib import Path

from agent_core.transport import (
    A2AEnvelope,
    AgentRegistry,
    HTTPAgentTransport,
    InboundA2AHandler,
)


def test_transport_conforms_to_reserved_protocol() -> None:
    """HTTPAgentTransport 结构化实现 reserved.AgentTransport Protocol。

    reserved 桩保持只读（无 runtime_checkable），故用静态赋值 + 方法存在性双验证。
    """
    from agent_core.reserved import AgentTransport

    transport: AgentTransport = HTTPAgentTransport(base_url="http://127.0.0.1:19999")
    assert callable(transport.send)
    assert callable(transport.receive)


def test_envelope_schema() -> None:
    """信封必含字段 + 默认值。"""
    env = A2AEnvelope(
        from_agent="agent_a", to_agent="agent_b",
        message_type="task_request", payload={"task": "extract"},
    )
    assert env.message_id  # 自动生成
    assert env.reply_to is None
    d = env.model_dump()
    assert d["from_agent"] == "agent_a"
    assert d["message_type"] == "task_request"
    assert "ts" in d


def test_registry_register_and_discover() -> None:
    """注册 Agent → 按 name 精确查 / 按 capability 模糊查。"""
    registry = AgentRegistry()
    registry.register("a1", capabilities=["extract"], endpoint="http://a1:8300")
    registry.register("a2", capabilities=["export"], endpoint="http://a2:8300")

    assert registry.get("a1")["endpoint"] == "http://a1:8300"
    assert registry.get("nonexistent") is None
    assert [a["name"] for a in registry.discover(capability="extract")] == ["a1"]
    assert registry.discover(capability="convert") == []
    registry.unregister("a1")
    assert registry.get("a1") is None


async def test_transport_roundtrip(tmp_path: Path) -> None:
    """send → 远端 handler 收到 envelope → reply 入收件箱 → receive 取出。"""
    received: list[A2AEnvelope] = []

    async def handler(envelope: A2AEnvelope) -> A2AEnvelope | None:
        received.append(envelope)
        return A2AEnvelope(
            from_agent=envelope.to_agent,
            to_agent=envelope.from_agent,
            message_type="reply",
            payload={"echo": envelope.payload},
            reply_to=envelope.message_id,
        )

    inbound = InboundA2AHandler(port=18400, handler=handler)
    await inbound.start()
    try:
        transport = HTTPAgentTransport(base_url="http://127.0.0.1:18400")
        env = A2AEnvelope(
            from_agent="a1", to_agent="a2",
            message_type="task_request", payload={"x": 1},
        )
        assert await transport.send(env) is None, "send 按 Protocol 返回 None"
        reply = await asyncio.wait_for(transport.receive(), timeout=5)
        assert reply["message_type"] == "reply"
        assert reply["payload"]["echo"]["x"] == 1
        assert reply["reply_to"] == env.message_id
        assert len(received) == 1
        assert received[0].from_agent == "a1"
    finally:
        await inbound.stop()


async def test_transport_receive_waits_then_gets_reply(tmp_path: Path) -> None:
    """receive 先于回复到达：协作等待（无自旋），回复后立即返回。"""

    async def handler(envelope: A2AEnvelope) -> A2AEnvelope:
        await asyncio.sleep(0.05)
        return A2AEnvelope(
            from_agent=envelope.to_agent, to_agent=envelope.from_agent,
            message_type="reply", payload={"late": True},
        )

    inbound = InboundA2AHandler(port=18401, handler=handler)
    await inbound.start()
    try:
        transport = HTTPAgentTransport(base_url="http://127.0.0.1:18401")
        receiver = asyncio.create_task(transport.receive())
        await asyncio.sleep(0.01)  # receiver 先挂起等待
        await transport.send(A2AEnvelope(
            from_agent="a", to_agent="b", message_type="query", payload={},
        ))
        reply = await asyncio.wait_for(receiver, timeout=5)
        assert reply["payload"] == {"late": True}
    finally:
        await inbound.stop()

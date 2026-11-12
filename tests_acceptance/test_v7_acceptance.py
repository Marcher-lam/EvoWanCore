"""T6 V7 验收套件：清债轮——孤儿收口/游标心跳/动态端口/鉴权/Realtime 协议。"""

import asyncio
import json
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout

# ---------------------------------------------------------------------------
# B1 孤儿任务收口（crash-sim 后无 pending 残留）
# ---------------------------------------------------------------------------

async def test_v7_no_orphan_tasks_after_crash(tmp_path: Path) -> None:
    from agent_core.models import InferResponse
    from agent_core.runtime.eventlog import SqliteEventLog
    from agent_sdk.api import AgentSDKServer

    block = asyncio.Event()

    def factory():
        from agent_core.capability import (
            CapabilityDescriptor,
            CapabilityType,
            InMemoryCapabilityRegistry,
        )
        from agent_core.evidence import SqliteEvidenceStore
        from agent_core.models import (
            FakeModelAdapter,
            Modality,
            ModelCapabilities,
            ModelGateway,
        )
        from agent_core.policy import DefaultPolicyEngine
        from agent_core.runtime.driver import AgentSpec, VerificationResult
        from agent_core.runtime.runtime import AgentRuntime
        from agent_core.runtime.statestore import SqliteStateStore

        class _OKVerifier:
            async def verify_action(self, request, result):
                return VerificationResult(passed=True)

            async def verify_task(self, task, evidence_refs):
                return VerificationResult(passed=True)

        registry = InMemoryCapabilityRegistry()

        async def hanging(args):
            await block.wait()
            return {"never": True}

        registry.bind(
            CapabilityDescriptor(
                id="hang", type=CapabilityType.TOOL, name="hang",
                input_schema={}, side_effect_level="read", auth_scope=[],
            ), hanging)
        fake = FakeModelAdapter(
            ModelCapabilities(
                model_id="m-orphan", inputs={Modality.TEXT},
                tools={"native_tool_call"},
            ),
            [
                InferResponse(tool_calls=[{"id": "c", "name": "hang", "arguments": {}}]),
                InferResponse(text="done"),
            ],
        )
        gateway = ModelGateway()
        gateway.register(fake)
        runtime = AgentRuntime(
            gateway=gateway, registry=registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=SqliteEventLog(tmp_path / "log.db"),
            state_store=SqliteStateStore(tmp_path / "state.db"),
            evidence_store=SqliteEvidenceStore(
                tmp_path / "ev.db", tmp_path / "blobs"),
            verifier=_OKVerifier(),
        )
        agent = AgentSpec(id="default", role="worker", model_id="m-orphan")
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    server = AgentSDKServer(factory)  # port=0
    await server.start()
    try:
        assert server.port > 0, "动态端口生效"
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "orphan", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]
            for _ in range(300):
                await asyncio.sleep(0.01)
                async with client.get(f"{server.url}/runs/{run_id}/events") as r:
                    types = [e["type"] for e in (await r.json())["events"]]
                if "ActionDispatched" in types:
                    await asyncio.sleep(0.1)
                    break
            bg = server._bg[run_id]  # noqa: SLF001 — crash-sim 注入点
            bg.cancel()
            try:
                await bg
            except asyncio.CancelledError:
                pass
            block.set()
        # ClientSession 已退出；给 aiohttp 连接收尾（FIN 传播）真实 I/O 时间
        await asyncio.sleep(0.2)
        current = asyncio.current_task()
        strays = [
            t for t in asyncio.all_tasks()
            if t is not current and not t.done()
        ]
        assert strays == [], (
            "crash-sim 后不得有孤儿任务: "
            f"{[(t.get_coro().__name__ if t.get_coro() else t) for t in strays]}"
        )
    finally:
        block.set()
        await server.stop()


# ---------------------------------------------------------------------------
# B2 SSE 游标 + 心跳（短心跳下悬挂 run 收到 : ping）
# ---------------------------------------------------------------------------

async def test_v7_sse_heartbeat_and_dynamic_port(tmp_path: Path) -> None:
    from agent_sdk.api import AgentSDKServer

    block = asyncio.Event()

    class _Hanging:
        def __init__(self) -> None:
            from agent_core.models import Modality, ModelCapabilities

            self._caps = ModelCapabilities(
                model_id="m-hb", inputs={Modality.TEXT},
                tools={"native_tool_call"},
            )

        def capabilities(self):
            return self._caps

        async def infer(self, request):
            from agent_core.models import InferResponse

            await block.wait()
            return InferResponse(text="done")

    from tests_acceptance.test_v6_acceptance import _base_factory

    base = _base_factory(tmp_path, responses=[])

    def factory():
        from agent_core.models import ModelGateway

        components = base()
        gw = ModelGateway()
        gw.register(_Hanging())
        components["runtime"]._gateway = gw  # noqa: SLF001
        return components

    server = AgentSDKServer(factory, sse_heartbeat_s=0.1)  # port=0
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "hb", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]
            got_ping = False
            async with client.get(
                f"{server.url}/runs/{run_id}/events/stream",
                timeout=ClientTimeout(total=5),
            ) as stream:
                assert stream.content_type == "text/event-stream"
                async for raw in stream.content:
                    line = raw.decode().strip()
                    if line.startswith(":") and "ping" in line:
                        got_ping = True
                        break
            assert got_ping, "空闲心跳帧未送达"
    finally:
        block.set()
        await server.stop()


# ---------------------------------------------------------------------------
# B3 A2A 鉴权红线（错 token 拒绝 + 正确 token 闭环）
# ---------------------------------------------------------------------------

async def test_v7_a2a_auth_redline() -> None:

    from agent_core.transport import (
        A2AEnvelope,
        HTTPAgentTransport,
        InboundA2AHandler,
    )

    async def handler(env: A2AEnvelope) -> A2AEnvelope:
        return A2AEnvelope(
            from_agent=env.to_agent, to_agent=env.from_agent,
            message_type="reply", payload={"auth": "ok"},
        )

    inbound = InboundA2AHandler(port=0, handler=handler, auth_token="k3y")
    await inbound.start()
    try:
        # 错 token → transport 收到 401 → raise_for_status 抛错（不得静默）
        bad = HTTPAgentTransport(f"http://127.0.0.1:{inbound.port}", auth_token="evil")
        try:
            await bad.send(A2AEnvelope(
                from_agent="x", to_agent="y", message_type="query", payload={},
            ))
            raise AssertionError("错 token 必须被拒绝")
        except Exception as e:  # noqa: BLE001 — aiohttp 4xx 异常类型不锁死
            assert "401" in str(e) or "Unauthorized" in str(e)

        # 正确 token → 闭环
        good = HTTPAgentTransport(f"http://127.0.0.1:{inbound.port}", auth_token="k3y")
        await good.send(A2AEnvelope(
            from_agent="x", to_agent="y", message_type="query", payload={},
        ))
        reply = await asyncio.wait_for(good.receive(), timeout=5)
        assert reply["payload"] == {"auth": "ok"}
    finally:
        await inbound.stop()


# ---------------------------------------------------------------------------
# B4 Realtime 协议链路（mock socket 端到端）
# ---------------------------------------------------------------------------

async def test_v7_realtime_protocol_over_mock() -> None:
    import websockets
    from agent_adapters.realtime_openai import OpenAIRealtimeAdapter

    auth_seen: list[str] = []

    async def _handler(ws):  # noqa: ANN001
        auth_seen.append(ws.request.headers.get("Authorization", ""))
        await ws.send(json.dumps({"type": "opened", "payload": {}}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "text":
                await ws.send(json.dumps({
                    "type": "transcript_out",
                    "payload": {"text": f"ack: {msg['text']}"},
                }))
            elif msg.get("type") == "close":
                await ws.send(json.dumps({"type": "closed", "payload": {}}))
                await ws.close()
                return

    server = await websockets.serve(_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        adapter = OpenAIRealtimeAdapter(
            endpoint=f"ws://127.0.0.1:{port}", api_key="sk-v7"
        )
        sid = await adapter.open_session(instructions="v7 验收")
        await adapter.send_text(sid, "ping")
        async for event in adapter.stream(sid):
            if event.type == "transcript_out" and "ack: ping" in str(event.payload):
                break
        else:
            raise AssertionError("echo 未到达")
        await adapter.close_session(sid)
        assert auth_seen == ["Bearer sk-v7"], "Bearer 头必须在 WebSocket 握手携带"
    finally:
        server.close()
        await server.wait_closed()

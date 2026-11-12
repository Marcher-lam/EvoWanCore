"""V6-T1 SSE 事件流：text/event-stream 实时推送 + 终态关闭 + 断连续游标。"""

import asyncio
import json
from pathlib import Path

from agent_sdk.api import AgentSDKServer
from aiohttp import ClientSession, ClientTimeout


def _make_app_factory(tmp_path: Path):
    """最小可运行 app 工厂（自包含，不跨测试包导入）。"""
    from agent_core.capability import (
        CapabilityDescriptor,
        CapabilityType,
        InMemoryCapabilityRegistry,
    )
    from agent_core.evidence import SqliteEvidenceStore
    from agent_core.models import (
        FakeModelAdapter,
        InferResponse,
        Modality,
        ModelCapabilities,
        ModelGateway,
    )
    from agent_core.policy import DefaultPolicyEngine
    from agent_core.runtime.driver import AgentSpec, VerificationResult
    from agent_core.runtime.eventlog import SqliteEventLog
    from agent_core.runtime.runtime import AgentRuntime
    from agent_core.runtime.statestore import SqliteStateStore

    class _OKVerifier:
        async def verify_action(self, request, result):
            return VerificationResult(passed=True)

        async def verify_task(self, task, evidence_refs):
            return VerificationResult(passed=True)

    def factory():
        registry = InMemoryCapabilityRegistry()

        async def handler(args):
            return {"ok": True}

        registry.bind(
            CapabilityDescriptor(
                id="noop", type=CapabilityType.TOOL, name="noop",
                input_schema={}, side_effect_level="read", auth_scope=[],
            ), handler)
        fake = FakeModelAdapter(
            ModelCapabilities(
                model_id="m-sdk", inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            [
                InferResponse(tool_calls=[{"id": "c1", "name": "noop", "arguments": {}}]),
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
            evidence_store=SqliteEvidenceStore(tmp_path / "ev.db", tmp_path / "blobs"),
            verifier=_OKVerifier(),
        )
        agent = AgentSpec(id="default", role="worker", model_id="m-sdk")
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    return factory


class _SlowAdapter:
    """慢模型：首次 infer 睡 0.2s——制造 SSE 实时观察窗口。"""

    def __init__(self) -> None:
        from agent_core.models import Modality, ModelCapabilities

        self._caps = ModelCapabilities(
            model_id="m-slow", inputs={Modality.TEXT}, tools={"native_tool_call"}
        )
        self._first = True

    def capabilities(self):
        return self._caps

    async def infer(self, request):
        from agent_core.models import InferResponse

        if self._first:
            await asyncio.sleep(0.2)
            self._first = False
            return InferResponse(
                tool_calls=[{"id": "c1", "name": "noop", "arguments": {}}]
            )
        await asyncio.sleep(0.05)
        return InferResponse(text="done")


def _slow_factory(tmp_path: Path):
    """在标准工厂上挂慢模型，default agent 指向它。"""
    from agent_core.models import ModelGateway
    from agent_core.runtime.driver import AgentSpec

    base = _make_app_factory(tmp_path)

    def factory():
        components = base()
        slow = _SlowAdapter()
        gateway: ModelGateway = components["runtime"]._gateway  # noqa: SLF001
        gateway.register(slow)
        components["agents"]["default"] = AgentSpec(
            id="default", role="worker", model_id="m-slow"
        )
        return components

    return factory


async def _read_stream(client, url: str, headers: dict | None = None):
    """读 SSE 到 event: end；返回 (data_events, saw_end)。"""
    data_events: list[dict] = []
    saw_end = False
    async with client.get(url, headers=headers or {},
                          timeout=ClientTimeout(total=15)) as resp:
        assert resp.status == 200
        assert resp.content_type == "text/event-stream"
        async for raw in resp.content:
            line = raw.decode().strip()
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload:
                    data_events.append(json.loads(payload))
            elif line.startswith("event:") and "end" in line:
                saw_end = True
                break
    return data_events, saw_end


async def test_sse_streams_live_events(tmp_path: Path) -> None:
    """运行中打开流 → 实时收到 RunCreated…RunVerified ≥3 事件 → end 关闭。"""
    server = AgentSDKServer(_slow_factory(tmp_path), port=18700)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "sse1", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]

            events, saw_end = await _read_stream(
                client, f"{server.url}/runs/{run_id}/events/stream"
            )
            assert saw_end, "终态后排空必须以 event: end 关闭"
            types = [e["type"] for e in events]
            assert len(events) >= 3
            assert "RunCreated" in types
            assert "RunVerified" in types
    finally:
        await server.stop()


async def test_sse_unknown_run_404(tmp_path: Path) -> None:
    server = AgentSDKServer(_make_app_factory(tmp_path), port=18701)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.get(f"{server.url}/runs/ghost/events/stream") as resp:
                assert resp.status == 404
    finally:
        await server.stop()


async def test_sse_last_event_id_resume(tmp_path: Path) -> None:
    """Last-Event-ID 头：从该事件之后重放（断线重连游标语义）。"""
    server = AgentSDKServer(_make_app_factory(tmp_path), port=18702)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "sse2", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]
            # 等终态
            for _ in range(250):
                async with client.get(f"{server.url}/runs/{run_id}") as r:
                    if (await r.json())["state"] == "succeeded":
                        break
                await asyncio.sleep(0.02)

            full, _ = await _read_stream(
                client, f"{server.url}/runs/{run_id}/events/stream"
            )
            assert len(full) >= 3
            mid_id = full[1]["id"]  # 第 2 个事件 id 作为断点

            resumed, saw_end = await _read_stream(
                client, f"{server.url}/runs/{run_id}/events/stream",
                headers={"Last-Event-ID": mid_id},
            )
            assert saw_end
            assert [e["id"] for e in resumed] == [e["id"] for e in full[2:]], \
                "只重放断点之后的事件"
    finally:
        await server.stop()

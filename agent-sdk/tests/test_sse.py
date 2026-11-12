"""V6-T1 SSE 事件流：text/event-stream 实时推送 + 终态关闭 + 断连续游标。

实时性验收（终评审修复后）：慢模型独占 gateway，断言首事件在 run 终态前到达——
缓冲到终态再吐的实现无法通过。
"""

import asyncio
import json
from pathlib import Path

from agent_sdk.api import AgentSDKServer
from aiohttp import ClientSession, ClientTimeout


def _make_app_factory(tmp_path: Path, responses: list | None = None):
    """最小可运行 app 工厂（自包含，不跨测试包导入）。

    responses：FakeModel 脚本（默认 1 工具调用 + 1 收口文本）；
    多 run 场景按 2 条/run 供给。
    """
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
        scripted = responses if responses is not None else [
            InferResponse(tool_calls=[{"id": "c1", "name": "noop", "arguments": {}}]),
            InferResponse(text="done"),
        ]
        fake = FakeModelAdapter(
            ModelCapabilities(
                model_id="m-sdk", inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            scripted,
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
    """慢模型：首次 infer 睡 0.15s——制造 SSE 实时观察窗口。"""

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
            await asyncio.sleep(0.15)
            self._first = False
            return InferResponse(
                tool_calls=[{"id": "c1", "name": "noop", "arguments": {}}]
            )
        await asyncio.sleep(0.03)
        return InferResponse(text="done")


def _slow_factory(tmp_path: Path):
    """慢模型独占 gateway（网关按注册序路由——追加注册会被快模型遮蔽）。"""
    from agent_core.models import ModelGateway

    base = _make_app_factory(tmp_path)

    def factory():
        components = base()
        gw = ModelGateway()
        gw.register(_SlowAdapter())
        components["runtime"]._gateway = gw  # noqa: SLF001 — 独占慢模型
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
    """运行中打开流 → 首事件在终态前实时到达 → 收满 ≥3 事件 → end 关闭。"""
    server = AgentSDKServer(_slow_factory(tmp_path), port=18700)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "sse1", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]

            # 手动增量读流：首事件到达时刻查 run 状态——必须尚未终态
            data_events: list[dict] = []
            saw_end = False
            state_at_first_event: str | None = None
            async with client.get(
                f"{server.url}/runs/{run_id}/events/stream",
                timeout=ClientTimeout(total=15),
            ) as stream:
                assert stream.status == 200
                assert stream.content_type == "text/event-stream"
                async for raw in stream.content:
                    line = raw.decode().strip()
                    if line.startswith("data:"):
                        payload = line[len("data:"):].strip()
                        if not payload:
                            continue
                        data_events.append(json.loads(payload))
                        if state_at_first_event is None:
                            async with client.get(
                                f"{server.url}/runs/{run_id}"
                            ) as r:
                                state_at_first_event = (await r.json())["state"]
                    elif line.startswith("event:") and "end" in line:
                        saw_end = True
                        break

            assert saw_end, "终态排空后必须以 event: end 关闭"
            assert len(data_events) >= 3
            types = [e["type"] for e in data_events]
            assert "RunCreated" in types and "RunVerified" in types
            # 实时性铁证：首事件到达时 run 仍在执行（非终态）
            assert state_at_first_event in ("queued", "running"), (
                f"SSE 未实时推送——首事件到达时 run 已终态: "
                f"{state_at_first_event}"
            )
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


async def test_sse_tail_events_not_lost(tmp_path: Path) -> None:
    """终评审 H1 回归：RunVerified 等尾事件必须在 event: end 之前送达。

    状态先落盘、尾事件后写——若关闭条件只看"终态+本轮无新事件"，
    会以概率丢尾。静默双拍确认后本测试应稳定全绿（连跑多次语义）。
    """
    from agent_core.models import InferResponse

    server = AgentSDKServer(_make_app_factory(
        tmp_path,
        responses=[
            InferResponse(tool_calls=[{"id": "c", "name": "noop", "arguments": {}}]),
            InferResponse(text="done"),
        ] * 5,  # 5 个 run × 2 条/run
    ), port=18703)
    await server.start()
    try:
        async with ClientSession() as client:
            run_ids = []
            for i in range(5):
                async with client.post(f"{server.url}/runs", json={
                    "task_id": f"sse-tail-{i}", "kind": "test", "goal": {},
                    "acceptance_criteria": [{"check": "done"}],
                }) as resp:
                    run_ids.append((await resp.json())["run_id"])
            for run_id in run_ids:
                events, saw_end = await _read_stream(
                    client, f"{server.url}/runs/{run_id}/events/stream"
                )
                assert saw_end
                types = [e["type"] for e in events]
                assert "RunVerified" in types, f"{run_id} 尾事件丢失"
                assert types[-1] in ("RunVerified", "RunFailed"), \
                    "终局事件必须最后送达"
    finally:
        await server.stop()

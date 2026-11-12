"""V6-T2 HTTP resume：crash-sim → POST /runs/{id}/resume + image_urls 注入。"""

import asyncio
from pathlib import Path

from agent_sdk.api import AgentSDKServer
from aiohttp import ClientSession


def _hanging_factory(tmp_path: Path, block: asyncio.Event):
    """悬停 handler：首个动作永不返回——制造可恢复的 RUNNING+pending 状态。"""
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

        async def hanging_handler(args):
            await block.wait()
            return {"never": "applied"}

        registry.bind(
            CapabilityDescriptor(
                id="hang", type=CapabilityType.TOOL, name="hang",
                input_schema={}, side_effect_level="read", auth_scope=[],
            ), hanging_handler)
        # 首响应派发悬停动作；恢复后第二响应 "done" 收口
        fake = FakeModelAdapter(
            ModelCapabilities(
                model_id="m-hang", inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            [
                InferResponse(tool_calls=[{"id": "c1", "name": "hang", "arguments": {}}]),
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
        agent = AgentSpec(id="default", role="worker", model_id="m-hang")
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    return factory


async def _wait_dispatched(client, url: str, run_id: str) -> None:
    """轮询直到 ActionDispatched，再等一拍让含 pending 的检查点落盘。

    事件先于检查点写入（runtime 在事件后的下一 await 落盘）——
    只等事件会在两者之间取消，resume 不发对账事件（flake 根因）。
    """
    for _ in range(300):
        await asyncio.sleep(0.01)
        async with client.get(f"{url}/runs/{run_id}/events") as resp:
            types = [e["type"] for e in (await resp.json())["events"]]
        if "ActionDispatched" in types:
            await asyncio.sleep(0.1)  # 检查点落盘余量
            return
    raise AssertionError("ActionDispatched 未出现")


async def test_resume_endpoint_e2e(tmp_path: Path) -> None:
    """crash-sim（杀后台任务）→ POST resume(202) → 轮询 succeeded + 对账事件。"""
    block = asyncio.Event()
    server = AgentSDKServer(_hanging_factory(tmp_path, block), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "r1", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                assert resp.status == 201
                run_id = (await resp.json())["run_id"]

            await _wait_dispatched(client, server.url, run_id)
            # 模拟进程崩溃：杀后台执行任务（回执丢失）
            assert run_id in server._bg, "后台任务应在运行"  # noqa: SLF001 — crash-sim
            bg = server._bg[run_id]  # noqa: SLF001
            bg.cancel()
            try:
                await bg
            except asyncio.CancelledError:
                pass
            block.set()  # 释放悬停 handler（其所属任务已死）

            # POST /runs/{id}/resume：202 + 后台恢复（与提交-轮询语义一致）
            async with client.post(
                f"{server.url}/runs/{run_id}/resume", json={"image_urls": {}},
            ) as resp:
                assert resp.status == 202
                assert (await resp.json())["state"] == "resuming"

            for _ in range(250):  # 轮询到终态
                async with client.get(f"{server.url}/runs/{run_id}") as r:
                    if (await r.json())["state"] == "succeeded":
                        break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("resume 后未到 succeeded")

            async with client.get(f"{server.url}/runs/{run_id}/events") as resp:
                types = [e["type"] for e in (await resp.json())["events"]]
            assert "ActionOutcomeUnknown" in types, "丢失回执必须对账标记"
    finally:
        await server.stop()


async def test_resume_while_still_executing_409(tmp_path: Path) -> None:
    """run 仍在后台执行（未崩溃）→ resume 拒绝 409（防双跑/副作用重复）。"""
    block = asyncio.Event()  # 不 set：handler 悬停，run 持续 RUNNING
    server = AgentSDKServer(_hanging_factory(tmp_path, block), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "r-live", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]
            await _wait_dispatched(client, server.url, run_id)

            async with client.post(
                f"{server.url}/runs/{run_id}/resume", json={},
            ) as resp:
                assert resp.status == 409
                assert "still executing" in (await resp.json())["error"]

            # 清理：放行 handler 让后台任务自然收口
            block.set()
    finally:
        await server.stop()


async def test_resume_unknown_run_404(tmp_path: Path) -> None:
    server = AgentSDKServer(_hanging_factory(tmp_path, asyncio.Event()), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs/ghost/resume", json={}) as resp:
                assert resp.status == 404
    finally:
        await server.stop()


async def test_resume_terminal_run_409(tmp_path: Path) -> None:
    """终态 run 不可恢复 → 409（pre-check 直接映射，无需触发 IllegalTransition）。"""
    block = asyncio.Event()
    block.set()  # handler 立即返回 → run 正常跑完到终态
    server = AgentSDKServer(_hanging_factory(tmp_path, block), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "r2", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]
            for _ in range(250):
                async with client.get(f"{server.url}/runs/{run_id}") as r:
                    if (await r.json())["state"] == "succeeded":
                        break
                await asyncio.sleep(0.02)
            async with client.post(f"{server.url}/runs/{run_id}/resume", json={}) as resp:
                assert resp.status == 409
    finally:
        await server.stop()

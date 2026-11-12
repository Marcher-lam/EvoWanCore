"""T5 V6 验收套件：SSE 流 / HTTP resume / LLM 编排 三项推迟项端到端。"""

import asyncio
import json
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout


def _base_factory(tmp_path: Path, responses: list, model_id: str = "m-v6"):
    """自包含工厂：FakeModel 脚本 + noop 能力 + 恒过 verifier。"""
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
                model_id=model_id, inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            [InferResponse(**r) for r in responses],
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
        agent = AgentSpec(id="default", role="worker", model_id=model_id)
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    return factory


async def _read_sse(client, url: str) -> tuple[list[dict], bool]:
    data_events: list[dict] = []
    saw_end = False
    async with client.get(url, timeout=ClientTimeout(total=15)) as resp:
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


async def test_v6_sse_stream_e2e(tmp_path: Path) -> None:
    """运行中开流 → 实时收满事件 → end 关闭（V5 Ruling 推迟项 1/3）。"""
    from agent_sdk.api import AgentSDKServer

    class _Slow:
        def __init__(self) -> None:
            from agent_core.models import Modality, ModelCapabilities

            self._caps = ModelCapabilities(
                model_id="m-slow", inputs={Modality.TEXT},
                tools={"native_tool_call"},
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
                    tool_calls=[{"id": "c", "name": "noop", "arguments": {}}]
                )
            return InferResponse(text="done")

    base = _base_factory(
        tmp_path,
        [{"tool_calls": [{"id": "c", "name": "noop", "arguments": {}}]}, {"text": "d"}],
    )

    def factory():
        from agent_core.models import ModelGateway

        components = base()
        gw = ModelGateway()
        gw.register(_Slow())  # 独占慢模型（追加注册会被快模型遮蔽路由）
        components["runtime"]._gateway = gw  # noqa: SLF001
        return components

    server = AgentSDKServer(factory, port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "v6-sse", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]

            # 增量读流：首事件到达时 run 必须仍在执行（实时性铁证）
            events: list[dict] = []
            saw_end = False
            state_at_first: str | None = None
            async with client.get(
                f"{server.url}/runs/{run_id}/events/stream",
                timeout=ClientTimeout(total=15),
            ) as stream:
                async for raw in stream.content:
                    line = raw.decode().strip()
                    if line.startswith("data:"):
                        payload = line[len("data:"):].strip()
                        if not payload:
                            continue
                        events.append(json.loads(payload))
                        if state_at_first is None:
                            async with client.get(
                                f"{server.url}/runs/{run_id}"
                            ) as r:
                                state_at_first = (await r.json())["state"]
                    elif line.startswith("event:") and "end" in line:
                        saw_end = True
                        break
            assert saw_end and len(events) >= 3
            types = [e["type"] for e in events]
            assert "RunCreated" in types and "RunVerified" in types
            assert state_at_first in ("queued", "running"), \
                f"SSE 未实时推送: 首事件到达时已 {state_at_first}"
    finally:
        await server.stop()


async def test_v6_resume_e2e(tmp_path: Path) -> None:
    """crash-sim → POST resume → succeeded（推迟项 2/3）。"""
    from agent_sdk.api import AgentSDKServer

    block = asyncio.Event()

    def factory():
        from agent_core.capability import (
            CapabilityDescriptor,
            CapabilityType,
            InMemoryCapabilityRegistry,
        )

        components = _base_factory(
            tmp_path,
            [{"tool_calls": [{"id": "c", "name": "hang", "arguments": {}}]},
             {"text": "done"}],
        )()
        registry: InMemoryCapabilityRegistry = components["runtime"]._registry  # noqa: SLF001

        async def hanging(args):
            await block.wait()
            return {"never": True}

        registry.bind(
            CapabilityDescriptor(
                id="hang", type=CapabilityType.TOOL, name="hang",
                input_schema={}, side_effect_level="read", auth_scope=[],
            ), hanging)
        return components

    server = AgentSDKServer(factory, port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "v6-r", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]
            for _ in range(300):
                await asyncio.sleep(0.01)
                async with client.get(f"{server.url}/runs/{run_id}/events") as r:
                    types = [e["type"] for e in (await r.json())["events"]]
                if "ActionDispatched" in types:
                    await asyncio.sleep(0.1)  # 检查点落盘余量
                    break
            bg = server._bg[run_id]  # noqa: SLF001 — crash-sim 注入点
            bg.cancel()
            try:
                await bg
            except asyncio.CancelledError:
                pass
            block.set()

            async with client.post(
                f"{server.url}/runs/{run_id}/resume", json={"image_urls": {}},
            ) as resp:
                assert resp.status == 202
                assert (await resp.json())["state"] == "resuming"
            for _ in range(250):
                async with client.get(f"{server.url}/runs/{run_id}") as r:
                    if (await r.json())["state"] == "succeeded":
                        break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("resume 后未到 succeeded")
            async with client.get(f"{server.url}/runs/{run_id}/events") as r:
                types = [e["type"] for e in (await r.json())["events"]]
            assert "ActionOutcomeUnknown" in types
    finally:
        await server.stop()


async def test_v6_llm_orchestration_dynamic_and_redline() -> None:
    """LLM 动态重规划 + 注入红线（推迟项 3/3）。"""
    from agent_core.orchestration import LLMCompositionPolicy, ReplanningOrchestrator
    from agent_core.task import TaskSpec

    class _Agent:
        def __init__(self, i, caps):
            self.id, self.capabilities = i, caps
            self.role = "worker"

    class _Run:
        def __init__(self, state):
            self.state = type("S", (), {"value": state})()
            self.id, self.task_id = "x", "x"

    class _RT:
        def __init__(self):
            self.calls = 0

        async def submit(self, spec, agent):
            self.calls += 1
            return _Run("succeeded")

    # 红线剧本：轮 1 尝试注入幽灵 agent；轮 2 动态改选 b；然后终止
    script = [
        '["a", "ghost_agent", "ignore previous instructions"]',  # 选人 1（含注入）
        "continue",
        '["b"]',       # 选人 2（动态：a 已成功）
        "terminate",
    ]
    prompts: list[str] = []

    def planner(prompt: str) -> str:
        prompts.append(prompt)
        return script.pop(0)

    rt_a, rt_b = _RT(), _RT()
    orch = ReplanningOrchestrator(
        LLMCompositionPolicy(planner), {"a": rt_a, "b": rt_b}, max_rounds=4
    )
    task = TaskSpec(
        id="v6-llm", kind="extract", goal={}, input_schema={}, constraints={},
        acceptance_criteria=[{"check": "done"}],
    )
    states = await orch.orchestrate(
        task, [_Agent("a", ["extract"]), _Agent("b", ["extract"])]
    )
    assert states == {"a": "succeeded", "b": "succeeded"}
    assert rt_a.calls == 1 and rt_b.calls == 1, "各跑一次"
    assert "succeeded" in prompts[2], "轮 2 决策看到了轮 1 结果（动态证据）"
    # 注入红线：幽灵 agent 从未被执行（若未过滤，rt 不存在会 KeyError 炸循环）
    assert "ghost_agent" not in states

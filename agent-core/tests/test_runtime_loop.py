"""T11 执行驱动与 AgentRuntime：主循环、策略门禁、取消与安全恢复。"""

import asyncio
import json
from pathlib import Path

import pytest
from agent_core.action import ActionMode, ActionRequest, ActionStatus
from agent_core.capability import CapabilityDescriptor, CapabilityType, InMemoryCapabilityRegistry
from agent_core.events import new_event  # noqa: F401 (复用事件构造断言)
from agent_core.evidence import SqliteEvidenceStore
from agent_core.models import (
    FakeModelAdapter,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
)
from agent_core.policy import DefaultPolicyEngine
from agent_core.runtime.driver import (
    AgentSpec,
    ExecutionPolicy,
    ModelLoopPolicy,
    VerificationResult,
)
from agent_core.runtime.eventlog import SqliteEventLog
from agent_core.runtime.executor import CapabilityExecutor
from agent_core.runtime.runtime import AgentRuntime
from agent_core.runtime.statestore import SqliteStateStore
from agent_core.task import TaskSpec

# ---------- 测试装置 ----------


class StubVerifier:
    def __init__(self, passed: bool = True) -> None:
        self._passed = passed

    async def verify_action(self, request, result) -> VerificationResult:  # noqa: ARG002
        return VerificationResult(passed=True, checks=[{"stub": True}])

    async def verify_task(self, task, evidence_refs) -> VerificationResult:  # noqa: ARG002
        return VerificationResult(
            passed=self._passed, checks=[{"stub": True}], evidence_refs=list(evidence_refs)
        )


def _spec() -> TaskSpec:
    return TaskSpec(
        id="spec_extract",
        kind="extract",
        goal={"what": "抽取发票字段"},
        input_schema={"doc": "asset_ref"},
        constraints={},
        acceptance_criteria=[{"check": "fields_present"}],
    )


def _tool_desc() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="extract_fields",
        type=CapabilityType.TOOL,
        name="字段抽取",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effect_level="read",
        auth_scope=[],
        tags=["doc"],
    )


def _skill_desc() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="skill_export",
        type=CapabilityType.SKILL,
        name="导出技能",
        input_schema={"type": "object"},
        side_effect_level="write",
        auth_scope=[],
    )


class Env:
    """组装一个最小运行时环境。"""

    def __init__(self, tmp_path: Path, responses, handler, verifier=None, granted=None):
        self.registry = InMemoryCapabilityRegistry()
        self.registry.bind(_tool_desc(), handler)
        self.registry.register(_skill_desc())

        self.gateway = ModelGateway()
        caps = ModelCapabilities(
            model_id="m-loop", inputs={Modality.TEXT}, tools={"native_tool_call"}
        )
        self.fake = FakeModelAdapter(caps, responses)
        self.gateway.register(self.fake)

        self.event_log = SqliteEventLog(tmp_path / "log.db")
        self.state_store = SqliteStateStore(tmp_path / "state.db")
        self.evidence = SqliteEvidenceStore(tmp_path / "ev.db", tmp_path / "blobs")
        self.runtime = AgentRuntime(
            gateway=self.gateway,
            registry=self.registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=self.event_log,
            state_store=self.state_store,
            evidence_store=self.evidence,
            verifier=verifier or StubVerifier(passed=True),
            granted_scopes=granted if granted is not None else [],
        )

    def agent(self) -> AgentSpec:
        return AgentSpec(
            id="agent_1", role="worker", capabilities=["extract_fields"], model_id="m-loop"
        )

    async def events(self, run_id: str) -> list:
        return [e async for e in self.event_log.iter_events(run_id)]


# ---------- 主循环 ----------


async def test_loop_solves_simple_task(tmp_path: Path) -> None:
    async def handler(args):
        return {"fields": {"total": "123"}}

    env = Env(
        tmp_path,
        responses=[
            InferResponse(
                tool_calls=[{"id": "c1", "name": "extract_fields", "arguments": {"doc": "a1"}}]
            ),
            InferResponse(text="done"),
        ],
        handler=handler,
    )
    run = await env.runtime.submit(_spec(), env.agent())
    assert run.state.value == "succeeded"
    events = await env.events(run.id)
    types = [e.type for e in events]
    assert "TaskCreated" in types
    assert "ActionDispatched" in types
    assert any(
        e.type == "RunStateChanged" and e.payload.get("state") == "succeeded" for e in events
    )
    # 因果链：每个动作事件都挂在 run 的 correlation 下
    assert all(e.correlation_id == run.id for e in events)
    # 成功动作留下证据引用
    dispatched = [e for e in events if e.type == "ActionDispatched"]
    assert len(dispatched) == 1
    assert dispatched[0].payload["capability_id"] == "extract_fields"


async def test_policy_denial_yields_rejected_action_not_crash(tmp_path: Path) -> None:
    async def handler(args):
        return {"ok": True}

    restricted = CapabilityDescriptor(
        id="restricted_tool",
        type=CapabilityType.TOOL,
        name="受限工具",
        input_schema={},
        side_effect_level="read",
        auth_scope=["secret:read"],
    )
    env = Env(
        tmp_path,
        responses=[
            # 第一轮就提议受限工具（未授予 secret:read）
            InferResponse(tool_calls=[{"id": "c2", "name": "restricted_tool", "arguments": {}}]),
            InferResponse(text="done"),
        ],
        handler=handler,
    )
    env.registry.bind(restricted, handler)
    run = await env.runtime.submit(_spec(), env.agent())
    events = await env.events(run.id)
    rejected = [e for e in events if e.type == "ActionRejected"]
    assert len(rejected) == 1
    assert "secret:read" in rejected[0].payload["reason"]
    # 循环没有崩溃，最终进入验证并按验证器结论落 FAIL（默认 stub=True → succeeded）
    assert run.state.value == "succeeded"


# ---------- Review Focus #1：取消与恢复 ----------


async def test_cancel_mid_dispatch_yields_outcome_unknown(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def slow_handler(args):
        await release.wait()
        return {"late": True}

    env = Env(
        tmp_path,
        responses=[
            InferResponse(tool_calls=[{"id": "c1", "name": "extract_fields", "arguments": {}}])
        ],
        handler=slow_handler,
    )
    submit_task = asyncio.create_task(env.runtime.submit(_spec(), env.agent()))
    # 等 ActionDispatched 出现（轮询事件日志）
    run_id = None
    for _ in range(200):
        await asyncio.sleep(0.01)
        async for e in env.event_log.iter_events():
            if e.type == "ActionDispatched":
                run_id = e.correlation_id
                break
        if run_id:
            break
    assert run_id, "动作始终未被派发"

    await env.runtime.cancel(run_id)
    release.set()  # 让迟到的回执可以到达（但已无意义）
    run = await submit_task

    assert run.state.value == "cancelled"
    events = await env.events(run_id)
    unknown = [e for e in events if e.type == "ActionOutcomeUnknown"]
    assert len(unknown) == 1  # 超时/取消 ≠ 失败：回执丢失语义
    assert unknown[0].payload["action_id"]


async def test_resume_does_not_refire_dispatched_actions(tmp_path: Path) -> None:
    block = asyncio.Event()

    async def hanging_handler(args):
        await block.wait()
        return {"never": "applied"}

    env = Env(
        tmp_path,
        responses=[
            InferResponse(tool_calls=[{"id": "c1", "name": "extract_fields", "arguments": {}}])
        ],
        handler=hanging_handler,
    )
    submit_task = asyncio.create_task(env.runtime.submit(_spec(), env.agent()))
    run_id = None
    for _ in range(200):
        await asyncio.sleep(0.01)
        async for e in env.event_log.iter_events():
            if e.type == "ActionDispatched":
                run_id = e.correlation_id
                break
        if run_id:
            break
    assert run_id

    # 模拟崩溃：直接取消 submit 任务（进程死掉，回执丢失）
    submit_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit_task
    block.set()

    # 新运行时实例，从持久化状态恢复
    env2 = Env(
        tmp_path,
        responses=[InferResponse(text="done")],  # 恢复后模型不再提议动作
        handler=hanging_handler,
    )
    env2.event_log = env.event_log
    env2.state_store = env.state_store
    env2.evidence = env.evidence
    env2.runtime.event_log = env.event_log
    env2.runtime.state_store = env.state_store
    env2.runtime.evidence_store = env.evidence

    run = await env2.runtime.resume(run_id)
    assert run.state.value == "succeeded"
    events = await env.events(run_id)
    dispatched = [e for e in events if e.type == "ActionDispatched"]
    assert len(dispatched) == 1, "恢复不得重复派发已 DISPATCHED 的动作"
    # 丢失回执的动作被标记 OUTCOME_UNKNOWN（RECONCILIATION 语义）
    assert any(e.type == "ActionOutcomeUnknown" for e in events)


# ---------- Executor 细节 ----------


async def test_skill_dispatch_default_denied(tmp_path: Path) -> None:
    async def handler(args):
        return {}

    env = Env(tmp_path, responses=[], handler=handler)
    executor = CapabilityExecutor(registry=env.registry, skill_runner=None)
    req = ActionRequest(
        id="act_s",
        task_id="t",
        run_id="r",
        actor_id="a",
        capability_id="skill_export",
        arguments={},
        mode=ActionMode.EXECUTE,
    )
    result = await executor.dispatch(req)
    assert result.status is ActionStatus.FAILED
    assert "skill" in result.error["reason"].lower() or "未配置" in result.error["reason"]


async def test_simulate_mode_returns_dry_run(tmp_path: Path) -> None:
    async def handler(args):
        return {"real": "effect"}

    env = Env(tmp_path, responses=[], handler=handler)
    executor = CapabilityExecutor(registry=env.registry, skill_runner=None)
    req = ActionRequest(
        id="act_sim",
        task_id="t",
        run_id="r",
        actor_id="a",
        capability_id="extract_fields",
        arguments={"doc": "a1"},
        mode=ActionMode.SIMULATE,
    )
    result = await executor.dispatch(req)
    assert result.status is ActionStatus.SUCCEEDED
    assert result.output["simulated"] is True
    assert env.fake.recorded_requests == []  # 干跑不触碰真实处理器之外的世界
    assert "real" not in json.dumps(result.output)


def test_reserved_contracts_importable() -> None:
    from agent_core.reserved import (  # noqa: F401
        AgentTransport,
        CompositionPolicy,
        EnvironmentAdapter,
        ProtocolAdapter,
    )

    assert ExecutionPolicy is not None
    assert ModelLoopPolicy is not None

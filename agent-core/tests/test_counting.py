"""T5 预算真实计量：CountingAdapter 计数 + Runtime 预算门（超限 FAILED）。"""

from pathlib import Path

from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)
from agent_core.evidence import SqliteEvidenceStore
from agent_core.models import (
    CountingAdapter,
    FakeModelAdapter,
    InferRequest,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
)
from agent_core.policy import DefaultPolicyEngine
from agent_core.run import RunState
from agent_core.runtime.driver import AgentSpec, VerificationResult
from agent_core.runtime.eventlog import SqliteEventLog
from agent_core.runtime.runtime import AgentRuntime
from agent_core.runtime.statestore import SqliteStateStore
from agent_core.task import TaskSpec


class _StubVerifier:
    async def verify_action(self, request, result):  # noqa: ARG002
        return VerificationResult(passed=True)

    async def verify_task(self, task, evidence_refs):  # noqa: ARG002
        return VerificationResult(passed=True)


class ActionMeter:
    """最小动作预算计（与 agent_explore.BudgetMeter 同鸭型：consume / exhausted）。"""

    def __init__(self, max_actions: int) -> None:
        self._limit = max_actions
        self.spent = 0

    def consume(self, *, actions: int = 0, **_: object) -> None:
        self.spent += actions

    @property
    def exhausted(self) -> bool:
        return self.spent >= self._limit


async def test_counting_adapter_counts() -> None:
    """每次 infer 计 1 次调用；total_tokens 按 usage 累计（缺失记 0）。"""
    caps = ModelCapabilities(model_id="m-count", inputs={Modality.TEXT})
    fake = FakeModelAdapter(
        caps,
        [
            InferResponse(text="a", usage={"total_tokens": 10}),
            InferResponse(text="b", usage={"total_tokens": 25, "prompt_tokens": 20}),
            InferResponse(text="c"),  # 无 usage → 记 0
        ],
    )
    counting = CountingAdapter(fake)
    assert counting.capabilities().model_id == "m-count"  # 能力委托内层
    req = InferRequest(model_id="m-count", messages=[{"role": "user", "content": "hi"}])
    await counting.infer(req)
    await counting.infer(req)
    await counting.infer(req)
    assert counting.calls == 3
    assert counting.total_tokens == 35


async def test_runtime_budget_exhaustion_fails_run(tmp_path: Path) -> None:
    """2 动作预算、3 提议：第 3 个动作不派发，run FAILED 且事件含 budget。"""
    registry = InMemoryCapabilityRegistry()

    async def handler(args):  # noqa: ARG001
        return {"ok": True}

    registry.bind(
        CapabilityDescriptor(
            id="noop_tool",
            type=CapabilityType.TOOL,
            name="空操作",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level="read",
            auth_scope=[],
        ),
        handler,
    )
    gateway = ModelGateway()
    fake = FakeModelAdapter(
        ModelCapabilities(
            model_id="m-budget", inputs={Modality.TEXT}, tools={"native_tool_call"}
        ),
        [
            InferResponse(
                tool_calls=[
                    {"id": "c1", "name": "noop_tool", "arguments": {}},
                    {"id": "c2", "name": "noop_tool", "arguments": {}},
                    {"id": "c3", "name": "noop_tool", "arguments": {}},
                ]
            ),
            InferResponse(text="done"),  # 若门失效会继续循环——兜底无工具调用转验证
        ],
    )
    gateway.register(fake)
    event_log = SqliteEventLog(tmp_path / "log.db")
    runtime = AgentRuntime(
        gateway=gateway,
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        event_log=event_log,
        state_store=SqliteStateStore(tmp_path / "state.db"),
        evidence_store=SqliteEvidenceStore(tmp_path / "ev.db", tmp_path / "blobs"),
        verifier=_StubVerifier(),
        budget_meter=ActionMeter(max_actions=2),
    )
    spec = TaskSpec(
        id="spec_budget",
        kind="noop",
        goal={"what": "x"},
        input_schema={},
        constraints={},
        acceptance_criteria=[{"check": "done"}],
    )
    run = await runtime.submit(spec, AgentSpec(id="a1", role="worker", model_id="m-budget"))

    assert run.state is RunState.FAILED
    dispatched = 0
    budget_marked = False
    async for e in event_log.iter_events(run.id):
        if e.type == "ActionDispatched":
            dispatched += 1
        if e.payload.get("reason") == "budget":
            budget_marked = True
    assert dispatched == 2, f"预算 2 却派发了 {dispatched} 个动作"
    assert budget_marked, "事件流应含 reason=budget"

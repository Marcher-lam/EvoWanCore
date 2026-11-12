"""T4 CompositionPolicy：能力匹配选参与者 + 终止判定。"""

from agent_core.orchestration import CapabilityCompositionPolicy


class _FakeAgent:
    def __init__(self, agent_id: str, capabilities: list[str]):
        self.id = agent_id
        self.capabilities = capabilities
        self.role = "worker"  # AgentSpec 兼容（Orchestrator 传入 submit）


class _FakeTask:
    def __init__(self, kind: str):
        self.kind = kind


def test_select_by_capability_match() -> None:
    """agent 声明的 capabilities 含 task kind → 入选。"""
    policy = CapabilityCompositionPolicy()
    agents = [
        _FakeAgent("a1", ["extract", "convert"]),
        _FakeAgent("a2", ["export"]),
        _FakeAgent("a3", ["extract", "validate"]),
    ]
    selected = policy.select_participants(_FakeTask("extract"), agents)
    assert set(selected) == {"a1", "a3"}, "两个 extract 能力 agent 均入选"


def test_select_no_match_returns_empty() -> None:
    """无人声明该能力 → 空列表。"""
    policy = CapabilityCompositionPolicy()
    agents = [_FakeAgent("a1", ["export"])]
    selected = policy.select_participants(_FakeTask("extract"), agents)
    assert selected == []


def test_select_empty_agents() -> None:
    policy = CapabilityCompositionPolicy()
    assert policy.select_participants(_FakeTask("extract"), []) == []


def test_decide_termination_all_done() -> None:
    """全部参与者到达终态 → terminate。"""
    policy = CapabilityCompositionPolicy()
    context = {
        "participants": {"a1": "succeeded", "a2": "failed"},
    }
    assert policy.decide_termination(_FakeTask("t"), context) == "terminate"


def test_decide_termination_still_running() -> None:
    policy = CapabilityCompositionPolicy()
    context = {
        "participants": {"a1": "succeeded", "a2": "running"},
    }
    assert policy.decide_termination(_FakeTask("t"), context) == "continue"


def test_decide_termination_empty() -> None:
    policy = CapabilityCompositionPolicy()
    assert policy.decide_termination(_FakeTask("t"), {"participants": {}}) == "terminate"


async def test_orchestrator_parallel(tmp_path) -> None:
    """T5：2 agent 各自完成子任务——并行 submit + 终态汇总。"""
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
    from agent_core.orchestration import Orchestrator
    from agent_core.policy import DefaultPolicyEngine
    from agent_core.runtime.driver import VerificationResult
    from agent_core.runtime.eventlog import SqliteEventLog
    from agent_core.runtime.runtime import AgentRuntime
    from agent_core.runtime.statestore import SqliteStateStore
    from agent_core.task import TaskSpec

    class _OKVerifier:
        async def verify_action(self, request, result):
            return VerificationResult(passed=True)

        async def verify_task(self, task, evidence_refs):
            return VerificationResult(passed=True)

    def _runtime(tag: str) -> AgentRuntime:
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
                model_id=f"m-{tag}", inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            [
                InferResponse(tool_calls=[{"id": "c", "name": "noop", "arguments": {}}]),
                InferResponse(text="done"),
            ],
        )
        gateway = ModelGateway()
        gateway.register(fake)
        return AgentRuntime(
            gateway=gateway, registry=registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=SqliteEventLog(tmp_path / f"{tag}_log.db"),
            state_store=SqliteStateStore(tmp_path / f"{tag}_state.db"),
            evidence_store=SqliteEvidenceStore(
                tmp_path / f"{tag}_ev.db", tmp_path / f"{tag}_blobs"),
            verifier=_OKVerifier(),
        )

    from agent_core.runtime.driver import AgentSpec as _AS

    agents = [
        _AS(id="worker_a", role="worker", capabilities=["extract"], model_id="m-a"),
        _AS(id="worker_b", role="worker", capabilities=["extract"], model_id="m-b"),
        _AS(id="worker_c", role="worker", capabilities=["export"], model_id="m-c"),
    ]
    runtimes = {
        "worker_a": _runtime("a"),
        "worker_b": _runtime("b"),
        "worker_c": _runtime("c"),
    }
    orchestrator = Orchestrator(CapabilityCompositionPolicy(), runtimes)
    task = TaskSpec(
        id="orch_test", kind="extract", goal={"what": "parallel"},
        input_schema={}, constraints={}, acceptance_criteria=[{"check": "done"}],
    )
    results = await orchestrator.orchestrate(task, agents)
    assert set(results.keys()) == {"worker_a", "worker_b"}, "只选 extract 能力者"
    assert all(s == "succeeded" for s in results.values())
    assert "worker_c" not in results, "export 能力 agent 不参与 extract 任务"

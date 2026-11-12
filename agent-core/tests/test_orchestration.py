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


# ---------- V6-T3/T4: LLM 动态调度 + 重规划循环 ----------

class _ScriptedPlanner:
    """按序弹出的假 LLM：记录 prompt 供断言。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        assert self._responses, "脚本耗尽"
        return self._responses.pop(0)


class _FakeRunHandle:
    def __init__(self, state: str) -> None:
        self.state = type("S", (), {"value": state})()


class _FakeRuntime:
    """计数式假 runtime：足够验证循环语义（真 runtime 由静态路径集成测试覆盖）。"""

    def __init__(self, final: str = "succeeded") -> None:
        self.calls = 0
        self.final = final

    async def submit(self, spec, agent) -> _FakeRunHandle:
        self.calls += 1
        return _FakeRunHandle(self.final)


async def test_llm_policy_selects_dynamically() -> None:
    """第 1 轮选 A；看到 A 成功的 context 后第 2 轮改选 B。"""
    from agent_core.orchestration import LLMCompositionPolicy

    planner = _ScriptedPlanner(['["a"]', '["b"]'])
    policy = LLMCompositionPolicy(planner)
    agents = [_FakeAgent("a", ["extract"]), _FakeAgent("b", ["export"])]

    r1 = await policy.select_participants(_FakeTask("extract"), agents, context={})
    assert r1 == ["a"]

    ctx = {"participants": {"a": "succeeded"}, "rounds": [{"a": "succeeded"}]}
    r2 = await policy.select_participants(_FakeTask("extract"), agents, context=ctx)
    assert r2 == ["b"]
    # prompt 必须带上 context（否则 LLM 无法动态决策）
    assert "succeeded" in planner.prompts[1]
    assert "a" in planner.prompts[0] and "b" in planner.prompts[0], "候选清单入 prompt"


async def test_llm_policy_rejects_phantom_agents() -> None:
    """幻觉 id 丢弃；全未知 → 空列表（本轮不派人）。"""
    from agent_core.orchestration import LLMCompositionPolicy

    planner = _ScriptedPlanner(['["a", "ghost", "9; rm -rf"]', '["phantom"]'])
    policy = LLMCompositionPolicy(planner)
    agents = [_FakeAgent("a", ["extract"])]

    assert await policy.select_participants(_FakeTask("t"), agents, context={}) == ["a"]
    assert await policy.select_participants(_FakeTask("t"), agents, context={}) == []


async def test_llm_policy_termination_failsafe() -> None:
    """"continue" 透传；垃圾输出 → terminate（fail-safe 停止）。"""
    from agent_core.orchestration import LLMCompositionPolicy

    planner = _ScriptedPlanner(["continue", "让子弹飞一会儿"])
    policy = LLMCompositionPolicy(planner)
    ctx = {"participants": {"a": "succeeded"}}
    assert await policy.decide_termination(_FakeTask("t"), ctx) == "continue"
    assert await policy.decide_termination(_FakeTask("t"), ctx) == "terminate"


async def test_llm_policy_bad_json_no_dispatch() -> None:
    """非 JSON 输出 → 空列表（不炸、不派错人）。"""
    from agent_core.orchestration import LLMCompositionPolicy

    planner = _ScriptedPlanner(["我认为应该让 a 上"])
    policy = LLMCompositionPolicy(planner)
    agents = [_FakeAgent("a", ["extract"])]
    assert await policy.select_participants(_FakeTask("t"), agents, context={}) == []


async def test_replanning_two_rounds() -> None:
    """轮 1 只跑 A，A 成功后重规划轮 2 只跑 B，终止。"""
    from agent_core.orchestration import LLMCompositionPolicy, ReplanningOrchestrator
    from agent_core.task import TaskSpec

    planner = _ScriptedPlanner([
        '["a"]',          # 轮 1 选人
        "continue",       # 轮 1 终止判定
        '["b"]',          # 轮 2 选人（动态：A 已成功）
        "terminate",      # 轮 2 终止判定
    ])
    policy = LLMCompositionPolicy(planner)
    rt_a, rt_b = _FakeRuntime("succeeded"), _FakeRuntime("succeeded")
    orch = ReplanningOrchestrator(policy, {"a": rt_a, "b": rt_b}, max_rounds=4)
    task = TaskSpec(
        id="rp1", kind="extract", goal={}, input_schema={}, constraints={},
        acceptance_criteria=[{"check": "done"}],
    )
    states = await orch.orchestrate(task, [
        _FakeAgent("a", ["extract"]), _FakeAgent("b", ["extract"]),
    ])
    assert states == {"a": "succeeded", "b": "succeeded"}
    assert rt_a.calls == 1 and rt_b.calls == 1, "各跑一次，无重复"
    # 轮 2 的选人 prompt 必须看到轮 1 结果（动态重规划的证据）
    assert "succeeded" in planner.prompts[2]


async def test_replanning_succeeded_not_rerun() -> None:
    """策略重复提名已成功者 → 不重跑（todo 过滤），无活可派即收口。"""
    from agent_core.orchestration import LLMCompositionPolicy, ReplanningOrchestrator
    from agent_core.task import TaskSpec

    planner = _ScriptedPlanner([
        '["a"]', "continue",
        '["a"]', "continue",   # 轮 2 仍提名 a（已成功）
    ])
    policy = LLMCompositionPolicy(planner)
    rt_a = _FakeRuntime("succeeded")
    orch = ReplanningOrchestrator(policy, {"a": rt_a}, max_rounds=4)
    task = TaskSpec(
        id="rp2", kind="extract", goal={}, input_schema={}, constraints={},
        acceptance_criteria=[{"check": "done"}],
    )
    states = await orch.orchestrate(task, [_FakeAgent("a", ["extract"])])
    assert states == {"a": "succeeded"}
    assert rt_a.calls == 1, "已成功者绝不重跑"


async def test_replanning_max_rounds_bound() -> None:
    """每轮失败的 agent 重试但有界：max_rounds 封顶。"""
    from agent_core.orchestration import LLMCompositionPolicy, ReplanningOrchestrator
    from agent_core.task import TaskSpec

    planner = _ScriptedPlanner(
        ['["a"]', "continue"] * 3 + ['["a"]', "terminate"]
    )
    policy = LLMCompositionPolicy(planner)
    rt_a = _FakeRuntime("failed")
    orch = ReplanningOrchestrator(policy, {"a": rt_a}, max_rounds=3)
    task = TaskSpec(
        id="rp3", kind="extract", goal={}, input_schema={}, constraints={},
        acceptance_criteria=[{"check": "done"}],
    )
    states = await orch.orchestrate(task, [_FakeAgent("a", ["extract"])])
    assert states == {"a": "failed"}
    assert rt_a.calls == 3, "失败者可重试，但 max_rounds 封顶"


async def test_static_policy_adapter_in_replanning_loop(tmp_path) -> None:
    """静态 CapabilityCompositionPolicy 经适配器跑同一重规划循环。"""
    from agent_core.orchestration import (
        CapabilityCompositionPolicy,
        ReplanningOrchestrator,
        StaticPolicyAdapter,
    )
    from agent_core.task import TaskSpec

    rt = _FakeRuntime("succeeded")
    orch = ReplanningOrchestrator(
        StaticPolicyAdapter(CapabilityCompositionPolicy()), {"a": rt}, max_rounds=2
    )
    task = TaskSpec(
        id="rp4", kind="extract", goal={}, input_schema={}, constraints={},
        acceptance_criteria=[{"check": "done"}],
    )
    states = await orch.orchestrate(task, [_FakeAgent("a", ["extract"])])
    assert states == {"a": "succeeded"}
    assert rt.calls == 1, "静态策略一轮终态即停"

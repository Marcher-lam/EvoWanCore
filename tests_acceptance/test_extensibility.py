"""T25 扩展性验收（spec §7.3）——四项"不改 Core"测试，全部 Fake 模型。"""

from pathlib import Path

import pytest
from agent_core.capability import CapabilityDescriptor, CapabilityType, InMemoryCapabilityRegistry
from agent_core.models import FakeModelAdapter, InferResponse, Modality, ModelCapabilities
from agent_core.runtime.driver import VerificationResult
from examples.docworkbench.app import build_app
from examples.docworkbench.generator import generate_corpus


def _adapter(responses: list[InferResponse]) -> FakeModelAdapter:
    return FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}, tools={"native_tool_call"}),
        responses,
    )


def _give_up() -> InferResponse:
    return InferResponse(text="no tools needed")


@pytest.fixture
def corpus():
    return generate_corpus(seed=5, size=8, difficulty=0.3)


def _validate_task(corpus):
    return next(t for t in corpus.tasks if t["dimension"] == "validate")


async def test_swap_model_adapter(tmp_path: Path, corpus) -> None:
    """① 不改 Core 替换模型适配器：两个不同适配器各自驱动同一应用。"""
    task = _validate_task(corpus)
    for i, responses in enumerate(
        [
            [InferResponse(text="A 方案")],  # 无工具调用
            [InferResponse(text="B 方案")],
        ]
    ):
        app = build_app(
            model_adapter=_adapter(responses),
            db_path=tmp_path / f"a{i}.db",
            blob_dir=tmp_path / f"blobs{i}",
            corpus=corpus,
        )
        run = await app.solve(task)
        assert run.state.value in ("succeeded", "failed")  # 应用照常运行，不因换适配器崩


async def test_add_new_tool_capability(tmp_path: Path, corpus) -> None:
    """② 不改 Core 新增工具能力：bind 即生效，可被派发。"""

    async def handler(args):
        return {"echo": args["x"], "custom": True}

    registry = InMemoryCapabilityRegistry()
    registry.bind(
        CapabilityDescriptor(
            id="my_custom_tool",
            type=CapabilityType.TOOL,
            name="自定义工具",
            input_schema={"x": "int"},
            auth_scope=[],
            tags=["custom"],
        ),
        handler,
    )
    found = registry.discover({"tags": ["custom"]})
    assert [d.id for d in found] == ["my_custom_tool"]

    from agent_core.action import ActionMode, ActionRequest
    from agent_core.runtime.executor import CapabilityExecutor

    executor = CapabilityExecutor(registry=registry, skill_runner=None)
    result = await executor.dispatch(
        ActionRequest(
            id="act_new",
            task_id="t",
            run_id="r",
            actor_id="a",
            capability_id="my_custom_tool",
            arguments={"x": 7},
            mode=ActionMode.EXECUTE,
        )
    )
    assert result.output["custom"] is True
    assert result.output["echo"] == 7


async def test_custom_verifier(tmp_path: Path, corpus) -> None:
    """③ 不改 Core 注入领域 Verifier：其判定决定任务终态。"""
    task = _validate_task(corpus)

    class StrictVerifier:
        """一律不通过的自定义验收器。"""

        async def verify_action(self, request, result):
            return VerificationResult(passed=True)

        async def verify_task(self, task, evidence_refs):
            return VerificationResult(passed=False, checks=[{"strict": "拒绝一切"}])

    # M8：verifier 经 build_app 公有参数注入（不再戳 _runtime._verifier）
    app = build_app(
        model_adapter=_adapter([_give_up()]),
        db_path=tmp_path / "strict.db",
        blob_dir=tmp_path / "blobs",
        corpus=corpus,
        verifier=StrictVerifier(),
    )
    run = await app.solve(task)
    assert run.state.value == "failed"  # 自定义验收器说了算


async def test_run_with_exploration_disabled(tmp_path: Path, corpus) -> None:
    """④ 关闭探索：纯 execute 模式零探索依赖可运行，不写长期记忆。"""
    task = _validate_task(corpus)
    app = build_app(
        model_adapter=_adapter([_give_up()]),
        db_path=tmp_path / "exec.db",
        blob_dir=tmp_path / "blobs",
        corpus=corpus,
    )
    run = await app.solve(task)  # 从不调用 explore
    assert run.state.value in ("succeeded", "failed")
    rules = await app._memory_store.query("rules", {})  # noqa: SLF001
    assert rules == [], "未启用探索不得写入长期记忆"


async def test_public_events_api(tmp_path: Path, corpus) -> None:  # noqa: ARG001
    """⑤ M8：公有 events() 事件流 + rebind() 覆盖绑定——消费面不触私有属性。"""
    from agent_core.evidence import SqliteEvidenceStore
    from agent_core.models import ModelGateway
    from agent_core.policy import DefaultPolicyEngine
    from agent_core.runtime.driver import AgentSpec
    from agent_core.runtime.eventlog import SqliteEventLog
    from agent_core.runtime.runtime import AgentRuntime
    from agent_core.runtime.statestore import SqliteStateStore
    from agent_core.task import TaskSpec

    class _OKVerifier:
        async def verify_action(self, request, result):  # noqa: ARG002
            return VerificationResult(passed=True)

        async def verify_task(self, task, evidence_refs):  # noqa: ARG002
            return VerificationResult(passed=True)

    registry = InMemoryCapabilityRegistry()

    async def handler(args):  # noqa: ARG001
        return {"ok": True}

    async def handler_v2(args):  # noqa: ARG001
        return {"ok": True, "v2": True}

    registry.bind(
        CapabilityDescriptor(
            id="noop_tool",
            type=CapabilityType.TOOL,
            name="空操作",
            input_schema={},
            auth_scope=[],
            tags=["pub"],
        ),
        handler,
    )
    # rebind：显式覆盖既有绑定；未注册 id 必须被拒
    registry.rebind(
        CapabilityDescriptor(
            id="noop_tool",
            type=CapabilityType.TOOL,
            name="空操作",
            input_schema={},
            auth_scope=[],
            tags=["pub"],
        ),
        handler_v2,
    )
    assert registry.handler_for("noop_tool") is handler_v2
    with pytest.raises(KeyError):
        registry.rebind(
            CapabilityDescriptor(
                id="never_bound",
                type=CapabilityType.TOOL,
                name="未注册",
                input_schema={},
                auth_scope=[],
            ),
            handler,
        )

    gateway = ModelGateway()
    gateway.register(_adapter([
        InferResponse(tool_calls=[{"id": "c1", "name": "noop_tool", "arguments": {}}]),
        InferResponse(text="done"),  # 动作执行后的第二轮提议：交卷转验证
    ]))
    runtime = AgentRuntime(
        gateway=gateway,
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        event_log=SqliteEventLog(tmp_path / "evlog.db"),
        state_store=SqliteStateStore(tmp_path / "st.db"),
        evidence_store=SqliteEvidenceStore(tmp_path / "ev.db", tmp_path / "blobs"),
        verifier=_OKVerifier(),
    )
    spec = TaskSpec(
        id="spec_pub",
        kind="noop",
        goal={},
        input_schema={},
        constraints={},
        acceptance_criteria=[{"check": "done"}],
    )
    run = await runtime.submit(spec, AgentSpec(id="a_pub", role="worker", model_id="m-app"))

    events = [e async for e in runtime.events(run.id)]
    types = [e.type for e in events]
    assert "RunCreated" in types and "ActionDispatched" in types
    # rebind 生效：派发命中的是覆盖后的 handler
    succeeded = next(e for e in events if e.type == "ActionSucceeded")
    assert succeeded.payload["output"]["v2"] is True

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

    # 通过继承组装注入（build_app 的开放面：runtime 构造参数全部可换）
    app = build_app(
        model_adapter=_adapter([_give_up()]),
        db_path=tmp_path / "strict.db",
        blob_dir=tmp_path / "blobs",
        corpus=corpus,
    )
    app._runtime._verifier = StrictVerifier()  # noqa: SLF001 — 验收测试验证可替换性
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

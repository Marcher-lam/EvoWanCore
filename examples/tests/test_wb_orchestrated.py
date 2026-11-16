"""V16-T1 docworkbench 编排：能力选人 + 并行批 + 失败重派 + 审计诊断。"""

import asyncio
from pathlib import Path


def _fixture(tmp_path: Path):
    """最小可跑 app（FakeVLM）+ corpus 任务。"""
    from agent_core.models import FakeModelAdapter, Modality, ModelCapabilities
    from examples.docworkbench.app import build_app
    from examples.docworkbench.corpus import split_corpus
    from examples.docworkbench.fake_vlm import WorkbenchFakeVLM
    from examples.docworkbench.generator import generate_corpus

    hard = generate_corpus(seed=41, size=8, difficulty=0.2, perceptual=True)
    practice, heldout = split_corpus(hard, practice_ratio=0.5)
    from agent_core.models import InferResponse

    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-orch", inputs={Modality.TEXT}),
        [InferResponse(text="done")] * 32,  # 每任务一轮即收口，批量够用
    )
    app = build_app(
        model_adapter=fake, db_path=tmp_path / "o.db", blob_dir=tmp_path / "ob",
        corpus=hard, practice_part=practice, heldout_part=heldout,
        vlm_factory=lambda c, d: WorkbenchFakeVLM(c, d),
    )
    return app, practice


def test_role_selection_by_dimension() -> None:
    """编排选人：wb:extract→solver-extract；wb:explore→solver-explore。"""
    from agent_core.orchestration import CapabilityCompositionPolicy
    from examples.docworkbench.orchestrated import wb_agents

    class _T:
        def __init__(self, kind):
            self.kind = kind

    policy = CapabilityCompositionPolicy()
    agents = wb_agents()
    assert policy.select_participants(_T("wb:extract"), agents) == \
        ["solver-extract"]
    assert policy.select_participants(_T("wb:explore"), agents) == \
        ["solver-explore"]


async def test_solve_batch_parallel_faster_than_serial(tmp_path: Path) -> None:
    """并行分派：批耗时显著低于串行（每 solve 有真实 await 段）。"""
    from examples.docworkbench.orchestrated import OrchestratedWorkbench

    app, practice = _fixture(tmp_path)

    class _SlowApp:  # 注入确定性延迟：并行/串行对比的公平前提
        def __init__(self, inner):
            self._inner = inner

        async def solve(self, t):
            await asyncio.sleep(0.05)
            return await self._inner.solve(t)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    slow = _SlowApp(app)
    orch = OrchestratedWorkbench(slow)
    tasks = practice.tasks[:4]

    import time

    t0 = time.monotonic()
    results = await orch.solve_batch(tasks)
    parallel_s = time.monotonic() - t0

    t1 = time.monotonic()
    for task in tasks:
        await slow.solve(task)
    serial_s = time.monotonic() - t1

    assert len(results) == 4
    assert all("state" in r for r in results)
    assert parallel_s < serial_s * 0.9, \
        f"并行应快于串行: {parallel_s:.2f}s vs {serial_s:.2f}s"


async def test_failure_redispatched_to_second_solver(tmp_path: Path) -> None:
    """失败重派：第一 solver 失败 → 重派第二 solver（T23 保证重跑安全）。"""
    from examples.docworkbench.orchestrated import OrchestratedWorkbench

    app, practice = _fixture(tmp_path)
    task = practice.tasks[0]
    calls: list[str] = []

    class _FlakyApp:
        """第一次 solve 失败，重试成功（模拟多 solver 差异性）。"""

        def __init__(self, inner):
            self._inner = inner
            self._first = True

        async def solve(self, t):
            calls.append("solve")
            if self._first:
                self._first = False
                from examples.docworkbench.orchestrated import FailedRun

                return FailedRun(t["id"])

            class _OK:  # 第二 solver 救回（成功 stub——重派语义即可测）

                class state:
                    value = "succeeded"

            return _OK()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    orch = OrchestratedWorkbench(_FlakyApp(app), max_retries=1)
    result = await orch.solve_one(task)
    assert calls == ["solve", "solve"], "失败后必须重派"
    assert result["state"] == "succeeded", "第二 solver 应救回"


async def test_auditor_diagnoses_failure_stage(tmp_path: Path) -> None:
    """审计角色：失败 run → 事件流诊断（失败阶段分类供学习反射器消费）。"""
    from examples.docworkbench.orchestrated import OrchestratedWorkbench

    app, practice = _fixture(tmp_path)
    # 用极难 corpus 让 solve 真失败
    from examples.docworkbench.corpus import split_corpus
    from examples.docworkbench.fake_vlm import WorkbenchFakeVLM
    from examples.docworkbench.generator import generate_corpus

    hard = generate_corpus(seed=41, size=8, difficulty=0.99, perceptual=True)
    practice_hard, _ = split_corpus(hard, practice_ratio=0.5)
    from agent_core.models import FakeModelAdapter, InferResponse, Modality, ModelCapabilities

    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-h", inputs={Modality.TEXT}),
        [InferResponse(text="done")] * 16,
    )
    from examples.docworkbench.app import build_app

    app_hard = build_app(
        model_adapter=fake, db_path=tmp_path / "h.db", blob_dir=tmp_path / "hb",
        corpus=hard, practice_part=practice_hard,
        vlm_factory=lambda c, d: WorkbenchFakeVLM(c, d),
    )
    task = next(
        t for t in practice_hard.tasks if t["dimension"] == "extract"
    )
    orch = OrchestratedWorkbench(app_hard, max_retries=0)
    result = await orch.solve_one(task)
    if result["state"] != "succeeded":
        assert "diagnosis" in result, "失败票必须带 auditor 诊断"
        assert result["diagnosis"]["stage"], "诊断含失败阶段"


async def test_orchestration_does_not_touch_northstar_path(
    tmp_path: Path,
) -> None:
    """T2 北极星保护：编排层不改 app 状态机——solve 原路径行为不变。"""
    from examples.docworkbench.orchestrated import OrchestratedWorkbench

    app, practice = _fixture(tmp_path)
    orch = OrchestratedWorkbench(app)
    task = practice.tasks[0]

    # 编排前后各直跑一次 solve：state 一致（无隐性状态污染）
    r1 = await app.solve(task)
    await orch.solve_one(task)
    r2 = await app.solve(task)
    assert r1.state.value == r2.state.value, "编排层不得改变 solve 行为"

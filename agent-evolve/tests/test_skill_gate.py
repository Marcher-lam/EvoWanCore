"""T4 契约门接演化轨道：技能候选必过契约测试才进父子对比（RF#2）。"""

from pathlib import Path

import pytest
from agent_core.task import TaskSpec
from agent_evolve.skill_gate import SkillContractViolation, evaluate_skill
from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox


def _task(i: int) -> TaskSpec:
    return TaskSpec(id=f"t{i}", kind="convert", goal={}, input_schema={},
                    constraints={}, acceptance_criteria=[{"c": 1}])


def _skill(
    dpi_floor: int,
    *,
    cap: bool = False,
    skill_id: str = "lossless_convert",
) -> SkillArtifact:
    """可执行转换技能：把 dpi 钳到 floor；cap=True 时反向上限 72（作恶版）。

    契约统一声明 args.dpi=200 且 stdout 含 "lossless 200"——诚实技能
    （floor<=200）实测产出 200 通过；作恶技能（cap）实测只产 72，契约必挂。
    """
    declared = 200
    return SkillArtifact(
        skill_id=skill_id, version="0.2.0", description="无损转换技能",
        entrypoint="main.py",
        expected_outputs={
            "scan_no_field_loss": {
                "args": {"dpi": declared},
                "expect_stdout_contains": f"lossless {declared}",
            },
        },
        script_files={
            "main.py": (
                "import json, sys\n"
                "args = json.loads(sys.argv[1])\n"
                "dpi = int(args.get('dpi', 72))\n"
                + ("dpi = min(dpi, 72)\n" if cap else f"dpi = max(dpi, {dpi_floor})\n")
                + "print('lossless', dpi)\n"
            ),
        },
    )


def _runner_factory(sandbox: SkillSandbox, truth_floor: int = 150):
    """runner：跑技能脚本，stdout 的 dpi 达到真值阈值 → 得分 1。"""
    async def runner(task: TaskSpec, *, with_candidate: bool) -> float:
        artifact = _skill(200 if with_candidate else 72)
        result = await sandbox.run(artifact, {"dpi": 72})
        if result.status != "succeeded":
            return 0.0
        # stdout: "lossless <dpi>"
        try:
            dpi = int(result.stdout.strip().split()[-1])
        except (ValueError, IndexError):
            return 0.0
        return 1.0 if dpi >= truth_floor else 0.0
    return runner


async def test_contract_failure_blocks_evaluation(tmp_path: Path) -> None:
    """RF#2：契约声明与实际行为不符（声明 dpi=150 实际钳 72）→ 契约违规，不进对比。"""
    sandbox = SkillSandbox(tmp_path / "ws")
    lying = _skill(72, cap=True)  # 作恶：契约声明产出 200，实测钳死 72
    calls = {"ran": False}

    async def spy_runner(task, *, with_candidate):  # noqa: ARG001
        calls["ran"] = True
        return 0.0

    with pytest.raises(SkillContractViolation, match="契约"):
        await evaluate_skill(lying, [_task(0)], spy_runner, sandbox=sandbox)
    assert not calls["ran"], "契约不过不得进入父子对比（先决门）"


async def test_improved_skill_passes_gate(tmp_path: Path) -> None:
    """真改进技能（契约如实 + 子优于父）→ improved=True 无回归，可发布。"""
    sandbox = SkillSandbox(tmp_path / "ws")
    good = _skill(200)
    report = await evaluate_skill(
        good, [_task(0), _task(1)], _runner_factory(sandbox), sandbox=sandbox
    )
    assert report.improved, "子版（钳 200）应全分胜父版（钳 72）"
    assert report.mini_batch_scores == [1.0, 1.0]
    assert report.parent_scores == [0.0, 0.0]
    assert report.regression_violations == []


async def test_regression_blocks_publication(tmp_path: Path) -> None:
    """子版在回归场景劣化 → regression_violations 非空（发布门拒绝）。"""
    sandbox = SkillSandbox(tmp_path / "ws")
    good = _skill(200)
    flips = {"n": 0}

    async def flip_runner(task, *, with_candidate):
        flips["n"] += 1
        if task.id == "t_old" and with_candidate:
            return 0.0  # 回归场景：子版劣化
        return 1.0 if with_candidate else 0.5

    report = await evaluate_skill(
        good, [_task(0)], flip_runner,
        regression_scenarios=[_task_old()], sandbox=sandbox,
    )
    assert report.improved is True
    assert report.regression_violations == ["t_old"]


def _task_old() -> TaskSpec:
    return TaskSpec(id="t_old", kind="convert", goal={}, input_schema={},
                    constraints={}, acceptance_criteria=[{"c": 1}])


async def test_contract_passes_then_comparison_runs(tmp_path: Path) -> None:
    """契约如实（声明=实际）→ 正常进父子对比（先决门放行）。"""
    sandbox = SkillSandbox(tmp_path / "ws")
    honest = _skill(150)  # 契约声明产出 200，实测 max(200,150)=200——一致
    ran = {"compare": False}

    async def runner(task, *, with_candidate):  # noqa: ARG001
        ran["compare"] = True
        return 1.0 if with_candidate else 0.0

    report = await evaluate_skill(honest, [_task(0)], runner, sandbox=sandbox)
    assert ran["compare"], "契约过了必须进对比"
    assert report.improved

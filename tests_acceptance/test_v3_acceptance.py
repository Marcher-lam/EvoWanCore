"""T11 V3 验收套件：真 VLM 北极星 + 技能演化双门 + 沙箱派发 + 回滚（RF 全覆盖）。"""

import json
from pathlib import Path

import pytest
from agent_core.action import ActionMode, ActionRequest
from agent_core.capability import InMemoryCapabilityRegistry
from agent_core.runtime.executor import CapabilityExecutor
from agent_core.task import TaskSpec
from agent_evolve.skill_gate import SkillContractViolation, evaluate_skill
from agent_evolve.skill_registry import SkillRegistryBridge
from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox


def _skill(floor: int, *, cap: bool = False) -> SkillArtifact:
    return SkillArtifact(
        skill_id="lossless_convert", version="0.1.0", description="d",
        entrypoint="main.py",
        expected_outputs={"ok": {"args": {"dpi": 200},
                                 "expect_stdout_contains": '"dpi": 200'}},
        script_files={"main.py": (
            "import json, sys\nargs = json.loads(sys.argv[1])\n"
            "dpi = int(args.get('dpi', 72))\n"
            + ("dpi = min(dpi, 72)\n" if cap else f"dpi = max(dpi, {floor})\n")
            + "print(json.dumps({'dpi': dpi}))\n")},
    )


def _t(i: str = "t0") -> TaskSpec:
    return TaskSpec(id=i, kind="convert", goal={}, input_schema={},
                    constraints={}, acceptance_criteria=[{"c": 1}])


async def test_skill_contract_gate_rf2(tmp_path: Path) -> None:
    """RF#2：契约不符（作恶 cap 版）→ SkillContractViolation，不进对比。"""
    sandbox = SkillSandbox(tmp_path / "ws")

    async def runner(task, *, with_candidate):  # noqa: ARG001
        return 0.0

    with pytest.raises(SkillContractViolation):
        await evaluate_skill(_skill(72, cap=True), [_t()], runner, sandbox=sandbox)


async def test_skill_dispatch_subprocess_rf3(tmp_path: Path) -> None:
    """RF#3：发布技能派发走子进程（网络 deny）——真跑出钳值结果。"""
    registry = InMemoryCapabilityRegistry()
    bridge = SkillRegistryBridge(
        sandbox=SkillSandbox(tmp_path / "ws"), registry=registry)
    bridge.register(_skill(200))
    executor = CapabilityExecutor(registry=registry, skill_runner=None)
    result = await executor.dispatch(ActionRequest(
        id="a", task_id="t", run_id="r", actor_id="x",
        capability_id="lossless_convert", arguments={"dpi": 72},
        mode=ActionMode.EXECUTE))
    assert result.status.value == "succeeded"
    assert result.output["dpi"] == 200


async def test_skill_rollback_rf4(tmp_path: Path) -> None:
    """RF#4：v2 发布 → 回滚 v1 → dispatch 命中 v1 行为。"""
    registry = InMemoryCapabilityRegistry()
    bridge = SkillRegistryBridge(
        sandbox=SkillSandbox(tmp_path / "ws"), registry=registry)
    v1, v2 = _skill(150), _skill(300)
    v2 = v2.model_copy(update={"version": "0.2.0"})
    bridge.register(v1)
    bridge.register(v2)
    assert registry.resolve("lossless_convert").version == "0.2.0"
    bridge.rollback(v1)
    executor = CapabilityExecutor(registry=registry, skill_runner=None)
    result = await executor.dispatch(ActionRequest(
        id="a", task_id="t", run_id="r", actor_id="x",
        capability_id="lossless_convert", arguments={"dpi": 72},
        mode=ActionMode.EXECUTE))
    assert result.output["dpi"] == 150, "回滚后 dispatch 命中 v1（floor=150）"


def test_v4_north_star_thresholds() -> None:
    """RF#1+5：v4 报告自动判定——explore extract ≥ 0.67、红线 0、comparable（调用计数）。"""
    report_path = Path("reports/exp_v4.json")
    if not report_path.exists():
        pytest.skip("reports/exp_v4.json 未生成（27b 实验进行中/未跑）")
    r = json.loads(report_path.read_text())
    explore = r["arms"]["explore"]
    assert r["comparable"] is True, "两臂调用计数预算可比"
    extract = explore["per_dimension"]["extract"]["mean"]
    assert extract >= 0.67, f"北极星（真 27b 读图）：explore extract {extract:.3f} < 0.67"
    workdirs = Path("reports/workdirs")
    if workdirs.exists():
        import asyncio

        from agent_core.memory.store import SqliteMemoryStore
        from examples.docworkbench.audit import audit_promoted

        async def _audit() -> float:
            total = wrong = 0
            for seed_dir in sorted(workdirs.glob("seed*")):
                mem = seed_dir / "explore" / "app.mem.db"
                if not mem.exists():
                    continue
                rep = await audit_promoted(SqliteMemoryStore(mem))
                total += rep.total
                wrong += rep.error_count
            return wrong / total if total else 0.0

        assert asyncio.run(_audit()) == 0.0, "红线：错误经验晋级率 = 0"

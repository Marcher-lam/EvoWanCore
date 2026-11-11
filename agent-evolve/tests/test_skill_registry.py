"""T5 发布注册：技能经演化门后注册为沙箱 capability（RF#3 派发即子进程）。"""

from pathlib import Path

from agent_core.action import ActionMode, ActionRequest
from agent_core.capability import InMemoryCapabilityRegistry
from agent_core.runtime.executor import CapabilityExecutor
from agent_evolve.skill_registry import SkillRegistryBridge
from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox


def _artifact(version: str, floor: int = 200) -> SkillArtifact:
    return SkillArtifact(
        skill_id="lossless_convert", version=version, description="无损转换技能",
        entrypoint="main.py",
        expected_outputs={"ok": {"args": {"dpi": 200}, "expect_stdout_contains": "lossless 200"}},
        script_files={
            "main.py": (
                "import json, sys\n"
                "args = json.loads(sys.argv[1])\n"
                f"dpi = max(int(args.get('dpi', 72)), {floor})\n"
                "print(json.dumps({'lossless': True, 'dpi': dpi}))\n"
            ),
        },
    )


async def test_published_skill_dispatchable_via_subprocess(tmp_path: Path) -> None:
    """RF#3：注册的技能 handler 走沙箱子进程（网络 deny 默认）——真跑成功。"""
    registry = InMemoryCapabilityRegistry()
    sandbox = SkillSandbox(tmp_path / "ws")
    bridge = SkillRegistryBridge(sandbox=sandbox, registry=registry)

    bridge.register(_artifact("0.2.0"))

    desc = registry.resolve("lossless_convert")
    assert desc.type.value == "tool"
    assert desc.version == "0.2.0"
    assert "skill" in desc.tags

    executor = CapabilityExecutor(registry=registry, skill_runner=None)
    result = await executor.dispatch(
        ActionRequest(
            id="act_skill_1", task_id="t", run_id="r", actor_id="a",
            capability_id="lossless_convert",
            arguments={"dpi": 72}, mode=ActionMode.EXECUTE,
        )
    )
    assert result.status.value == "succeeded"
    assert result.output["dpi"] == 200, "技能脚本钳值生效（子进程真跑）"
    assert result.output["lossless"] is True


async def test_skill_version_bumps_and_old_handler_replaced(tmp_path: Path) -> None:
    """再发布 → version 递增且 handler 换新（旧版行为不残留）。"""
    registry = InMemoryCapabilityRegistry()
    sandbox = SkillSandbox(tmp_path / "ws")
    bridge = SkillRegistryBridge(sandbox=sandbox, registry=registry)

    bridge.register(_artifact("0.1.0", floor=150))
    bridge.register(_artifact("0.2.0", floor=300))

    desc = registry.resolve("lossless_convert")
    assert desc.version == "0.2.0", "再发布 version 递增（描述符层面）"

    executor = CapabilityExecutor(registry=registry, skill_runner=None)
    result = await executor.dispatch(
        ActionRequest(
            id="act_skill_2", task_id="t", run_id="r", actor_id="a",
            capability_id="lossless_convert",
            arguments={"dpi": 72}, mode=ActionMode.EXECUTE,
        )
    )
    assert result.output["dpi"] == 300, "dispatch 命中新版脚本（floor=300）"


async def test_rollback_restores_incumbent(tmp_path: Path) -> None:
    """RF#4：发布 v2 → rollback → dispatch 命中 v1 行为（旧版可用，无旧版则注销）。"""
    from agent_core.action import ActionMode, ActionRequest
    from agent_core.runtime.executor import CapabilityExecutor

    registry = InMemoryCapabilityRegistry()
    sandbox = SkillSandbox(tmp_path / "ws")
    bridge = SkillRegistryBridge(sandbox=sandbox, registry=registry)

    v1 = _artifact("0.1.0", floor=150)
    v2 = _artifact("0.2.0", floor=300)
    bridge.register(v1)
    bridge.register(v2)
    assert registry.resolve("lossless_convert").version == "0.2.0"

    bridge.rollback(v1)  # 回滚到 v1
    desc = registry.resolve("lossless_convert")
    assert desc.version == "0.1.0", "回滚后描述符回到 v1"

    executor = CapabilityExecutor(registry=registry, skill_runner=None)
    result = await executor.dispatch(
        ActionRequest(
            id="act_skill_rb", task_id="t", run_id="r", actor_id="a",
            capability_id="lossless_convert",
            arguments={"dpi": 72}, mode=ActionMode.EXECUTE,
        )
    )
    assert result.output["dpi"] == 150, "dispatch 命中 v1 行为（floor=150）"

    # 回滚到不存在更早版本的场景：注销技能（resolve 抛 KeyError）
    bridge.unregister()
    import pytest as _pytest

    with _pytest.raises(KeyError):
        registry.resolve("lossless_convert")

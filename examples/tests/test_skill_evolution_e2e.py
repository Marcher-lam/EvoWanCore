"""T7 技能演化载体 e2e（Fake 确定性版）：平庸技能失败 → SkillReflector 产出改进
→ 契约门 + 演化门双过 → 发布注册 → dispatch 真跑子进程。DGM-lite 管道钉子。"""

import json
from pathlib import Path

from agent_core.action import ActionMode, ActionRequest
from agent_core.capability import InMemoryCapabilityRegistry
from agent_core.models import (
    FakeModelAdapter,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
)
from agent_core.runtime.executor import CapabilityExecutor
from agent_core.task import TaskSpec
from agent_evolve.skill_gate import evaluate_skill
from agent_evolve.skill_registry import SkillRegistryBridge
from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox
from agent_explore.skill_reflector import SkillReflector


class _Fam:
    value = "variant"


def _mediocre_v1() -> SkillArtifact:
    """平庸 v0.1.0：dpi 钳 72（扫描件丢字段——契约与演化门都会挂）。"""
    return SkillArtifact(
        skill_id="lossless_convert", version="0.1.0",
        description="平庸版：默认 dpi",
        entrypoint="main.py",
        expected_outputs={"ok": {"args": {"dpi": 200}, "expect_stdout_contains": "lossless 200"}},
        script_files={
            "main.py": (
                "import json, sys\n"
                "args = json.loads(sys.argv[1])\n"
                "dpi = min(int(args.get('dpi', 72)), 72)\n"
                "print(json.dumps({'lossless': dpi >= 150, 'dpi': dpi}))\n"
            ),
        },
    )


def _reflector_response() -> str:
    """LLM（Fake 脚本）对失败练习的反思产出：v0.2.0 钳 150。"""
    return json.dumps({
        "skill": {
            "skill_id": "lossless_convert", "version": "0.2.0",
            "description": "改进版：dpi 提升到 150 保字段",
            "entrypoint": "main.py",
            "expected_outputs": {
                "scan_no_field_loss": {"args": {"dpi": 200},
                                       "expect_stdout_contains": '"dpi": 200'},
            },
            "known_failure_scenarios": [],
            "script_files": {
                "main.py": (
                    "import json, sys\n"
                    "args = json.loads(sys.argv[1])\n"
                    "dpi = max(int(args.get('dpi', 72)), 150)\n"
                    "print(json.dumps({'lossless': dpi >= 150, 'dpi': dpi}))\n"
                ),
            },
        }
    })


def _task(i: int) -> TaskSpec:
    return TaskSpec(id=f"t{i}", kind="convert", goal={}, input_schema={},
                    constraints={}, acceptance_criteria=[{"c": 1}])


async def test_skill_evolution_end_to_end(tmp_path: Path) -> None:
    sandbox = SkillSandbox(tmp_path / "ws")
    registry = InMemoryCapabilityRegistry()
    bridge = SkillRegistryBridge(sandbox=sandbox, registry=registry)

    # 1) 初始平庸 v0.1.0 发布（incumbent）
    bridge.register(_mediocre_v1())

    # 2) 演化门 runner：技能跑真值语义（dpi>=150 → no_field_loss）
    async def runner(task: TaskSpec, *, with_candidate: bool) -> float:  # noqa: ARG001
        artifact = _reflector_output if with_candidate else _mediocre_v1()
        result = await sandbox.run(artifact, {"dpi": 72})
        try:
            out = json.loads(result.stdout.strip().splitlines()[-1])
            return 1.0 if out.get("lossless") else 0.0
        except (ValueError, IndexError):
            return 0.0

    # 3) SkillReflector 从失败产出改进候选（Fake LLM 脚本）
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-sr", inputs={Modality.TEXT}),
        [InferResponse(text=_reflector_response())],
    )
    gw = ModelGateway()
    gw.register(fake)
    reflector = SkillReflector(gateway=gw, model_id="m-sr")

    task_stub = type("T", (), {"id": "convert_doc_2", "title": "无损转换",
                               "family": _Fam()})()
    outcome = type("O", (), {
        "task_id": "convert_doc_2", "title": "无损转换", "status": "failed",
        "evidence_refs": ["e1"],
        "actions": [{"capability_id": "convert_format",
                     "arguments": {"doc_id": "doc_2", "dpi": 72},
                     "status": "succeeded"}],
        "verifier_checks": [{"check": "no_field_loss",
                             "detail": "转换不得丢字段(dpi>=150)"}],
    })()
    candidate = await reflector(task_stub, outcome)
    assert candidate is not None
    _reflector_output = candidate.materialize()  # noqa: F841 — runner 闭包引用

    # 4) 双门：契约（子进程实测）→ 演化（父子对比）
    report = await evaluate_skill(
        _reflector_output, [_task(0), _task(1)], runner, sandbox=sandbox,
    )
    assert report.improved, "v0.2.0 应胜 v0.1.0"
    assert report.regression_violations == []

    # 5) 发布注册 + dispatch 真跑
    bridge.register(_reflector_output)
    assert registry.resolve("lossless_convert").version == "0.2.0"
    executor = CapabilityExecutor(registry=registry, skill_runner=None)
    result = await executor.dispatch(
        ActionRequest(id="act_evo", task_id="t", run_id="r", actor_id="a",
                      capability_id="lossless_convert",
                      arguments={"dpi": 72}, mode=ActionMode.EXECUTE))
    assert result.status.value == "succeeded"
    assert result.output == {"lossless": True, "dpi": 150}, (
        "演化后技能 dispatch 真跑：钳 150 保字段（子进程）"
    )

"""T3 SkillReflector：练习失败 → 技能代码候选（DGM-lite 的产出端）。"""

import json

from agent_core.models import (
    FakeModelAdapter,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
)
from agent_explore.skill_reflector import SkillCandidate, SkillReflector


class _Fam:
    value = "variant"


class _Task:
    id = "convert_doc_2"
    title = "无损转换练习"
    family = _Fam()
    objective = {"what": "无损转换"}


class _Outcome:
    task_id = "convert_doc_2"
    title = "无损转换练习"
    status = "failed"
    evidence_refs = ["e1"]
    actions = [
        {"capability_id": "convert_format", "arguments": {"doc_id": "doc_2", "dpi": 72},
         "status": "succeeded"},
    ]
    verifier_checks = [{"check": "no_field_loss", "detail": "转换不得丢字段(dpi>=150)"}]


def _gateway(responses):
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-sr", inputs={Modality.TEXT}), list(responses)
    )
    gw = ModelGateway()
    gw.register(fake)
    return gw, fake


def _valid_candidate_json() -> str:
    return json.dumps({
        "skill": {
            "skill_id": "lossless_convert",
            "version": "0.2.0",
            "description": "无损转换：扫描件 dpi 提升到 150 以上再转换",
            "entrypoint": "main.py",
            "expected_outputs": {
                "scan_no_field_loss": {
                    "args": {"doc_id": "doc_2", "dpi": 150},
                    "expect_stdout_contains": "lossless",
                },
            },
            "known_failure_scenarios": [],
            "script_files": {
                "main.py": (
                    "import json, sys\n"
                    "args = json.loads(sys.argv[1])\n"
                    "dpi = max(int(args.get('dpi', 72)), 150)\n"
                    "print('lossless', dpi)\n"
                ),
            },
        }
    })


async def test_skill_reflector_produces_artifact() -> None:
    """合法 JSON → 可物化 SkillArtifact（entrypoint 在 script_files 中）。"""
    gw, _fake = _gateway([InferResponse(text=_valid_candidate_json())])
    reflector = SkillReflector(gateway=gw, model_id="m-sr")

    candidate = await reflector(_Task(), _Outcome())
    assert isinstance(candidate, SkillCandidate)
    art = candidate.materialize()  # 跨包物化 SkillArtifact（agent-evolve 侧模型）
    assert art.skill_id == "lossless_convert"
    assert art.entrypoint == "main.py"
    assert art.entrypoint in art.script_files, "entrypoint 必须存在于脚本文件集"
    assert art.script_files["main.py"].startswith("import json")
    assert art.expected_outputs["scan_no_field_loss"]["expect_stdout_contains"] == "lossless"
    # 契约非空（M10 空契约必 fail 门在下游，产出端就不得产空契约）
    assert art.expected_outputs or art.known_failure_scenarios


async def test_skill_reflector_malformed_returns_none() -> None:
    """畸形输出两轮 → None（不抛错）；无证据 → 直接 None。"""
    gw, fake = _gateway(
        [InferResponse(text="not json"), InferResponse(text="still not json")]
    )
    reflector = SkillReflector(gateway=gw, model_id="m-sr")
    assert await reflector(_Task(), _Outcome()) is None
    assert len(fake.recorded_requests) == 2, "畸形应触发一次校验-重试"

    no_evidence = type("O", (), {
        "task_id": "t", "title": "t", "status": "failed",
        "evidence_refs": [], "actions": [], "verifier_checks": [],
    })()
    gw2, fake2 = _gateway([InferResponse(text=_valid_candidate_json())])
    reflector2 = SkillReflector(gateway=gw2, model_id="m-sr")
    assert await reflector2(_Task(), no_evidence) is None, "无证据不产技能（宁可不学）"
    assert len(fake2.recorded_requests) == 0, "无证据应短路，不烧调用"


async def test_skill_reflector_empty_entrypoint_rejected() -> None:
    """entrypoint 不在 script_files → 校验失败重试；仍坏 → None。"""
    bad = json.loads(_valid_candidate_json())
    bad["skill"]["entrypoint"] = "missing.py"
    gw, _fake = _gateway([
        InferResponse(text=json.dumps(bad)),
        InferResponse(text=json.dumps(bad)),
    ])
    reflector = SkillReflector(gateway=gw, model_id="m-sr")
    assert await reflector(_Task(), _Outcome()) is None

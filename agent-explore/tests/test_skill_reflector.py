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


async def test_type_deviation_triggers_retry() -> None:
    """真模型缺陷（DeepSeek 实测）：expected_outputs 给 list、failure_scenarios 给
    字符串——校验必须在反思循环内按 SkillArtifact 实模校验并触发重试，而不是
    漏到 materialize() 运行时才炸。"""
    bad = json.loads(_valid_candidate_json())["skill"]
    bad["expected_outputs"] = [{"args": {"dpi": 150}}]  # list 而非 dict
    bad["known_failure_scenarios"] = ["dpi< 150"]  # 字符串而非 dict
    good = json.loads(_valid_candidate_json())
    gw, fake = _gateway([
        InferResponse(text=json.dumps({"skill": bad})),
        InferResponse(text=json.dumps(good)),
    ])
    reflector = SkillReflector(gateway=gw, model_id="m-sr")
    candidate = await reflector(_Task(), _Outcome())
    assert candidate is not None, "类型偏差第一轮 → 带错误重试 → 第二轮合法 → 产出"
    assert len(fake.recorded_requests) == 2
    candidate.materialize()  # 不再运行时炸


async def test_contract_failure_triggers_revision(tmp_path) -> None:
    """L2 换法：契约实测进反思循环——round1 契约挂（声明 200 实产 72）→ 喂实测
    错误修订 → round2 契约过 → 产出（门降级为纵深防御）。"""
    from agent_evolve.skill_sandbox import SkillSandbox

    def _candidate(floor: int) -> dict:
        return json.dumps({"skill": {
            "skill_id": "lossless_convert", "version": "0.2.0",
            "description": "d", "entrypoint": "main.py",
            "expected_outputs": {"ok": {"args": {"dpi": 200},
                                        "expect_stdout_contains": '"dpi": 200'}},
            "known_failure_scenarios": [],
            "script_files": {"main.py": (
                "import json, sys\n"
                "args = json.loads(sys.argv[1])\n"
                f"dpi = min(int(args.get('dpi', 72)), {floor})\n"
                "print(json.dumps({'dpi': dpi}))\n")},
        }})

    gw, fake = _gateway([
        InferResponse(text=_candidate(72)),   # 契约挂：实产 72
        InferResponse(text=_candidate(200)),  # 修订：实产 200
    ])
    sandbox = SkillSandbox(tmp_path / "ws")
    reflector = SkillReflector(gateway=gw, model_id="m-sr", sandbox=sandbox)

    candidate = await reflector(_Task(), _Outcome())
    assert candidate is not None, "契约失败应触发修订而非直接放弃"
    art = candidate.materialize()
    report = await sandbox.contract_test(art)
    assert report.passed, "返回的候选必须契约已验证"
    assert len(fake.recorded_requests) == 2
    # 修订请求携带实测错误（round2 提示含失败原因）
    assert "72" in fake.recorded_requests[1].messages[-1]["content"], (
        "修订提示须携带实测 stdout（真模型据此定位）"
    )


async def test_lineage_enforced_against_id_hijack(tmp_path) -> None:
    """RF#5 血统：模型擅改 skill_id → 代码层强制收回 incumbent id + 同名版本 bump。"""
    from agent_evolve.skill_sandbox import SkillSandbox

    gw, _fake = _gateway([InferResponse(text=_valid_candidate_json())])
    reflector = SkillReflector(gateway=gw, model_id="m-sr",
                               sandbox=SkillSandbox(tmp_path / "ws"))
    incumbent = SkillCandidate(
        artifact={"skill_id": "incumbent_skill", "version": "1.2.3",
                  "description": "d", "entrypoint": "main.py",
                  "expected_outputs": {}, "known_failure_scenarios": [],
                  "script_files": {"main.py": "print('ok')"}},
        source_task_id="t",
    )
    task = type("T", (), {"id": "convert_doc_2", "title": "练", "family": _Fam()})()
    candidate = await reflector(task, _Outcome(), incumbent=incumbent)
    assert candidate is not None
    art = candidate.artifact
    assert art["skill_id"] == "incumbent_skill", "模型改名必须被收回（血统）"
    # _valid_candidate_json 声明 version=0.2.0 ≠ incumbent 1.2.3 → 保留模型版（非同名）
    assert art["version"] == "0.2.0"

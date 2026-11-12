"""SkillReflector — 练习失败 → 技能代码候选（V3 §5.2 B 块，DGM-lite 产出端）。

产出只是候选：发布必过双门（SkillSandbox 契约测试 + 演化门父子对比，T4/T5）。
契约声明与验收脚本语义同源（no_field_loss 等）；空契约不产（M10 门在下游必 fail，
产出端直接不生成空契约候选）。无证据 → None（宁可不学，不可编造——沿记忆 reflector 纪律）。
"""

from __future__ import annotations

import json
from typing import Protocol

from agent_core.models import InferRequest, Modality, ModelGateway
from pydantic import BaseModel, ValidationError

from agent_explore.curriculum import CurriculumGenerationError  # noqa: F401 — 语义对齐引用


class ReflectorError(Exception):
    """技能候选输出两轮校验失败。"""


def _bump(version: str) -> str:
    """patch 位 +1（版本被沿用时的确定性递增）。"""
    try:
        parts = version.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    except (ValueError, IndexError):
        return version + ".1"


class _ArtifactPayload(BaseModel):
    """LLM 输出契约：{"skill": SkillArtifact 字段}——校验后物化 SkillArtifact。"""

    skill: dict

    @staticmethod
    def json_schema_hint() -> dict:
        """嵌套真实 SkillArtifact schema——内层字段类型对模型可见（真模型实测：
        只包外壳时 expected_outputs 常给 list、failure_scenarios 给字符串）。"""
        from agent_evolve.skill_sandbox import SkillArtifact

        schema = _ArtifactPayload.model_json_schema()
        inner = SkillArtifact.model_json_schema()
        schema["properties"]["skill"] = inner
        if "required" in inner:
            schema["properties"]["skill"]["required"] = inner["required"]
        return {"name": "SkillCandidateOutput", "schema": schema}


class _TaskLike(Protocol):
    id: str
    title: str

    @property
    def family(self) -> object: ...


class _OutcomeLike(Protocol):
    status: str
    evidence_refs: list[str]
    actions: list[dict]
    verifier_checks: list[dict]


class SkillCandidate(BaseModel):
    """技能候选 = SkillArtifact + 来源（练习 outcome）——演化轨道的输入。"""

    artifact: dict  # SkillArtifact 字段集（agent-evolve 侧物化，避免跨包模型依赖）
    source_task_id: str
    source_evidence_refs: list[str] = []

    def materialize(self):
        from agent_evolve.skill_sandbox import SkillArtifact

        return SkillArtifact(**self.artifact)


def _tolerant_validate(text: str) -> _ArtifactPayload:
    """容错解析：真模型常在 JSON 字符串值里输出字面换行（脚本代码未转义）——
    strict 解析报 EOF 假截断；strict=False 允许串内控制字符后按 dict 校验。"""
    try:
        return _ArtifactPayload.model_validate_json(text)
    except ValidationError:
        obj = json.loads(text, strict=False)
        return _ArtifactPayload.model_validate(obj)


def _validate(payload: _ArtifactPayload) -> str | None:
    """结构合法后的实模校验：SkillArtifact 模型 + 语义（entrypoint/契约非空）。

    实模校验必须在循环内（真模型的类型偏差——expected_outputs 给 list、
    failure_scenarios 给字符串——要触发带错误的重试，而非 materialize 运行时炸）。
    """
    from agent_evolve.skill_sandbox import SkillArtifact

    try:
        artifact = SkillArtifact(**payload.skill)
    except ValidationError as e:
        return f"SkillArtifact 字段类型不符: {e}"
    entry = artifact.entrypoint
    if entry not in artifact.script_files:
        return f"entrypoint {entry!r} 不在 script_files 中"
    if not artifact.expected_outputs and not artifact.known_failure_scenarios:
        return "契约声明为空（expected_outputs 与 failure_scenarios 皆空）"
    return None


class SkillReflector:
    """LLM 反思的技能代码版：失败练习（trace + 验收反馈）→ 可执行改进技能。"""

    def __init__(
        self, *, gateway: ModelGateway, model_id: str, sandbox=None
    ) -> None:
        self._gateway = gateway
        self._model_id = model_id
        self._sandbox = sandbox  # 可选：注入后启用 verify-and-revise（契约实测进循环）

    async def __call__(
        self, task: _TaskLike, outcome: _OutcomeLike, incumbent: SkillCandidate | None = None
    ) -> SkillCandidate | None:
        if not outcome.evidence_refs:
            return None  # 无真实证据不产技能（宁可不学，不可编造）
        actions = list(getattr(outcome, "actions", []) or [])
        checks = list(getattr(outcome, "verifier_checks", []) or [])
        incumbent_hint = ""
        if incumbent is not None:
            inc_art = incumbent.materialize()
            incumbent_hint = (
                " CURRENT SKILL (revise this, do not write from scratch): "
                + json.dumps(
                    {"entrypoint": inc_art.entrypoint,
                     "script_files": inc_art.script_files},
                    ensure_ascii=False,
                )
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the skill reflector for a document-workbench agent. A practice "
                    "call to a TOOL failed. Write an improved SKILL (self-contained python, "
                    + incumbent_hint
                    + " "
                    "stdin-argv JSON in / stdout out) that performs the tool's job "
                    "correctly. Ground the fix in the action trace and acceptance "
                    "feedback. The skill MUST declare its contract — declare a MINIMAL "
                    "contract you are CERTAIN the script satisfies (1-2 simple "
                    "expected_outputs; avoid speculative failure_scenarios — every "
                    "declared case is verified in a real subprocess and any miss "
                    "rejects the whole skill). Your contract MUST include the exact "
                    "FAILING call from the trace as a case (same args), with the "
                    "CORRECTED expected output that demonstrates the fix. Contract "
                    "case format: "
                    'expected_outputs.<case> = {"args": {<命令行参数，脚本从 '
                    'argv[1] 收 JSON 字符串>}, "expect_stdout_contains": "<stdout '
                    '必须包含的原文子串>"}}. The script receives args as a JSON '
                    "string in argv[1] (NOT stdin). Output ONLY JSON: "
                    '{"skill": {"skill_id", "version", "description", "entrypoint", '
                    '"expected_outputs", "known_failure_scenarios", "script_files"}}. '
                    "The script runs in a network-denied subprocess; only stdlib. "
                    "CRITICAL: the skill must work for ANY document of this type "
                    "(not just the one in the trace) — no hard-coded doc_ids."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": {"id": task.id, "title": task.title},
                        "failed_action_trace": actions,
                        "acceptance_feedback": checks,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        base_msgs = list(messages)
        request_msgs = list(messages)
        for _schema_attempt in range(2):
            adapter = self._gateway.route({"inputs": [Modality.TEXT]})
            resp = await adapter.infer(
                InferRequest(
                    model_id=adapter.capabilities().model_id,
                    messages=request_msgs,
                    structured_output=_ArtifactPayload.json_schema_hint(),
                    max_tokens=8192,  # 长脚本产出：供应商默认会截断（实测 EOF@~1.6k 字符）
                )
            )
            try:
                payload = _tolerant_validate(resp.text)
            except (json.JSONDecodeError, ValidationError) as e:
                request_msgs = base_msgs + [
                    {
                        "role": "user",
                        "content": (
                            f"上一次输出未通过 JSON/schema 校验（{type(e).__name__}: {e}）。"
                            "请严格按 schema 输出合法 JSON。"
                        ),
                    }
                ]
                continue
            semantic_error = _validate(payload)
            if semantic_error:
                request_msgs = base_msgs + [
                    {
                        "role": "user",
                        "content": f"上一次输出未通过校验（{semantic_error}）。请修正。",
                    }
                ]
                continue

            skill = dict(payload.skill)
            if incumbent is not None:
                # DGM 血统纪律：修订保持同一 skill_id（版本演进）——模型擅自改名
                # 会注册成新技能、旧技能原地不动（attempt 10 实测）。代码强制，不信回显。
                inc_id = incumbent.artifact.get("skill_id")
                if inc_id and skill.get("skill_id") != inc_id:
                    skill["skill_id"] = inc_id
                if skill.get("version") == incumbent.artifact.get("version"):
                    skill["version"] = _bump(incumbent.artifact.get("version", "0.0.0"))
            candidate = SkillCandidate(
                artifact=skill,
                source_task_id=task.id,
                source_evidence_refs=list(outcome.evidence_refs),
            )
            # verify-and-revise（L2）：契约实测进循环——失败喂实测结果修订一轮。
            # 技能门（T4）保留为纵深防御；反射器产出契约已验证。
            if self._sandbox is None:
                return candidate
            for _contract_attempt in range(2):
                artifact = candidate.materialize()
                contract = await self._sandbox.contract_test(artifact)
                if contract.passed:
                    return candidate
                failed = [c for c in contract.cases if not c["ok"]]
                if _contract_attempt == 1:
                    return None  # 修订后仍不过 → 放弃（宁可不学）
                diagnosis = []
                for name, spec in artifact.expected_outputs.items():
                    run_result = await self._sandbox.run(
                        artifact, spec.get("args", {})
                    )
                    diagnosis.append(
                        {"case": name, "args": spec.get("args", {}),
                         "expected": spec.get("expect_stdout_contains"),
                         "actual_stdout": run_result.stdout[:400]}
                    )
                request_msgs = base_msgs + [
                    {
                        "role": "user",
                        "content": (
                            "你上一版技能的契约在真实子进程实测中失败："
                            + json.dumps(
                                {"failed_cases": [c["name"] for c in failed],
                                 "diagnosis": diagnosis},
                                ensure_ascii=False,
                            )
                            + "。请修订脚本或契约（宁可收缩契约到你能确保的行为），"
                            "输出完整候选 JSON。"
                        ),
                    }
                ]
                resp = await adapter.infer(
                    InferRequest(
                        model_id=adapter.capabilities().model_id,
                        messages=request_msgs,
                        structured_output=_ArtifactPayload.json_schema_hint(),
                        max_tokens=8192,
                    )
                )
                try:
                    payload = _tolerant_validate(resp.text)
                    if _validate(payload) is None:
                        skill = dict(payload.skill)
                        if incumbent is not None:
                            inc_id = incumbent.artifact.get("skill_id")
                            if inc_id:
                                skill["skill_id"] = inc_id
                        candidate = SkillCandidate(
                            artifact=skill,
                            source_task_id=task.id,
                            source_evidence_refs=list(outcome.evidence_refs),
                        )
                except (json.JSONDecodeError, ValidationError, ValueError):
                    pass  # 修订输出无效 → 维持旧候选进下一轮实测（必挂→放弃）
        return None  # schema 两轮失败 → 无候选

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


class _ArtifactPayload(BaseModel):
    """LLM 输出契约：{"skill": SkillArtifact 字段}——校验后物化 SkillArtifact。"""

    skill: dict


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


def _validate(payload: _ArtifactPayload) -> str | None:
    """结构合法后的语义校验：entrypoint 必在 script_files；契约非空。"""
    skill = payload.skill
    entry = skill.get("entrypoint")
    files = skill.get("script_files") or {}
    if not entry or entry not in files:
        return f"entrypoint {entry!r} 不在 script_files 中"
    if not skill.get("expected_outputs") and not skill.get("known_failure_scenarios"):
        return "契约声明为空（expected_outputs 与 failure_scenarios 皆空）"
    return None


class SkillReflector:
    """LLM 反思的技能代码版：失败练习（trace + 验收反馈）→ 可执行改进技能。"""

    def __init__(self, *, gateway: ModelGateway, model_id: str) -> None:
        self._gateway = gateway
        self._model_id = model_id

    async def __call__(self, task: _TaskLike, outcome: _OutcomeLike) -> SkillCandidate | None:
        if not outcome.evidence_refs:
            return None  # 无真实证据不产技能（宁可不学，不可编造）
        actions = list(getattr(outcome, "actions", []) or [])
        checks = list(getattr(outcome, "verifier_checks", []) or [])
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the skill reflector for a document-workbench agent. A practice "
                    "call to a TOOL failed. Write an improved SKILL (self-contained python, "
                    "stdin-argv JSON in / stdout out) that performs the tool's job "
                    "correctly. Ground the fix in the action trace and acceptance "
                    "feedback. The skill MUST declare its contract: expected_outputs "
                    "(args + expect_stdout_contains) and/or known_failure_scenarios — an "
                    "empty contract is invalid. Output ONLY JSON: "
                    '{"skill": {"skill_id", "version", "description", "entrypoint", '
                    '"expected_outputs", "known_failure_scenarios", "script_files"}}. '
                    "The script runs in a network-denied subprocess; only stdlib."
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
        request_msgs = list(messages)
        last_error = ""
        for _attempt in range(2):
            adapter = self._gateway.route({"inputs": [Modality.TEXT]})
            resp = await adapter.infer(
                InferRequest(
                    model_id=adapter.capabilities().model_id,
                    messages=request_msgs,
                    structured_output={
                        "name": "SkillCandidateOutput",
                        "schema": _ArtifactPayload.model_json_schema(),
                    },
                )
            )
            try:
                payload = _ArtifactPayload.model_validate_json(resp.text)
                semantic_error = _validate(payload)
                if semantic_error:
                    raise ValueError(semantic_error)
                return SkillCandidate(
                    artifact=payload.skill,
                    source_task_id=task.id,
                    source_evidence_refs=list(outcome.evidence_refs),
                )
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_error = f"{type(e).__name__}: {e}"
                request_msgs = list(messages) + [
                    {
                        "role": "user",
                        "content": (
                            f"上一次输出未通过校验（{last_error}）。"
                            "请严格按 schema 输出合法 JSON，不要输出其他内容。"
                        ),
                    }
                ]
        return None  # 两轮失败 → 无候选（调用方保持现状）

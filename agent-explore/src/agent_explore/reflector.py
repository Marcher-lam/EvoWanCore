"""LLMReflector — 轨迹→候选经验的真 LLM 反思（V2 §5.1 B 块）。

结构化声明双层（§6.3）：机器可判的 claim（dimension/action/params）+ 人读 statement。
产出仅是候选（CANDIDATE）——发布权在 EvolutionController 通用评测门（T12），
本模块不做门禁。evidence 白名单：引用只取 outcome.evidence_refs，LLM 自报一律丢弃。
"""

from __future__ import annotations

import json
from typing import Protocol

from agent_core.models import InferRequest, Modality, ModelGateway
from pydantic import BaseModel, ValidationError


class ReflectorError(Exception):
    """反思输出两轮校验失败。"""


class ReflectedProposal(BaseModel):
    statement: str
    claim: dict = {}  # {"dimension", "action", "params"}
    applicability: dict = {}


class ReflectorOutput(BaseModel):
    proposals: list[ReflectedProposal] = []

    @staticmethod
    def json_schema_hint() -> dict:
        return {"name": "ReflectorOutput", "schema": ReflectorOutput.model_json_schema()}


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


async def _infer_validated(gateway: ModelGateway, messages: list[dict], schema_hint: dict,
                           result_cls: type[BaseModel]):
    """带校验-重试的 infer（PracticeWave 同款）：畸形输出附错误重试一次，再失败抛错。"""
    request_msgs = list(messages)
    last_error = ""
    for _attempt in range(2):
        adapter = gateway.route({"inputs": [Modality.TEXT]})
        resp = await adapter.infer(
            InferRequest(
                model_id=adapter.capabilities().model_id,
                messages=request_msgs,
                structured_output=schema_hint,
            )
        )
        try:
            return result_cls.model_validate_json(resp.text)
        except (json.JSONDecodeError, ValidationError) as e:
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
    raise ReflectorError(f"反思输出两轮校验失败: {last_error}")


class LLMReflector:
    """controller 反射协议的真 LLM 实现（同步外观 + awaitable 语义兼容）。"""

    def __init__(self, *, gateway: ModelGateway, model_id: str) -> None:
        self._gateway = gateway
        self._model_id = model_id

    async def __call__(self, task: _TaskLike, outcome: _OutcomeLike) -> list[dict]:
        if not outcome.evidence_refs:
            return []  # 无真实证据不产生记忆（宁可不学，不可编造）
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the experience reflector for a document-workbench agent "
                    "whose ONLY tools are: parse_document(doc_id); extract_fields"
                    "(doc_id, region); convert_format(doc_id, dpi); export_document"
                    "(doc_id, format); validate_form(doc_id). From the practice "
                    "outcome, its ACTION TRACE, and the ACCEPTANCE FEEDBACK (verifier "
                    "checks with failure details — this is the environment telling you "
                    "WHAT went wrong), infer reusable CAUSAL rules about "
                    "HOW TO CALL these tools — which parameter values decide success "
                    "and why. GROUNDING DISCIPLINE: cite only parameters that appear "
                    "(or are conspicuously defaulted) in the trace; never invent "
                    "parameter stories for arguments you did not observe. When a scan "
                    "document's fields were misread or its export lost pages, consider "
                    "the preprocessing lever convert_format(doc_id, dpi) — the right "
                    "threshold is yours to hypothesize; the evaluation gate will "
                    "verify behaviorally. Each rule MUST carry a structured claim: "
                    '{"dimension": one of export|extract|convert|validate, "action": '
                    'one of the tool names above, "params": the decisive parameter '
                    "values} — plus a human-readable statement. Generic process wisdom "
                    "(e.g. 'verify afterwards', 'check the format') is NOT acceptable. "
                    "An empty proposals list is acceptable when the trace supports no "
                    "causal rule."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": {
                            "id": task.id,
                            "family": getattr(task.family, "value", str(task.family)),
                            "title": task.title,
                        },
                        "outcome": {
                            "status": outcome.status,
                            "evidence_ref_count": len(outcome.evidence_refs),
                        },
                        "action_trace": list(getattr(outcome, "actions", []) or []),
                        "acceptance_feedback": list(
                            getattr(outcome, "verifier_checks", []) or []
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            parsed = await _infer_validated(
                self._gateway, messages, ReflectorOutput.json_schema_hint(), ReflectorOutput
            )
        except ReflectorError:
            return []
        return [
            {
                "kind": "rule",
                "content": {"statement": p.statement, "claim": p.claim},
                "applicability": p.applicability,
                "evidence_refs": list(outcome.evidence_refs),  # 白名单：仅 outcome 的证据
            }
            for p in parsed.proposals
        ]

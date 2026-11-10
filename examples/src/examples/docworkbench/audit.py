"""audit_promoted — 晋级记忆正确性审计（spec §7.2）。

真值来源：tools.HIDDEN_CONSTRAINTS（可审计常量表）+ 可注入的强模型复核。
错误经验晋级率是 V1 主指标之一：宁可少学，不可学错。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

from agent_core.memory.record import MemoryRecord, VerificationState
from agent_core.memory.store import NS_RULES, MemoryStore
from agent_core.models import InferRequest, Modality, ModelGateway
from pydantic import BaseModel

from examples.docworkbench.tools import HIDDEN_CONSTRAINTS

# 每条真值约束的判定关键词（小写包含匹配，全部命中才算"陈述了该真值"）
_TRUTH_KEYWORDS: dict[str, list[str]] = {
    "export_needs_convert": ["convert_format", "200"],
    "extract_two_column_region": ["region", "left"],
    "validate_tmp_prefix": ["tmp"],
    "convert_low_dpi_loses_fields": ["150"],
}

_REVIEWER = Callable[[str], bool] | None


class AuditReport(BaseModel):
    total: int = 0
    error_count: int = 0
    error_promotion_rate: float = 0.0
    wrong_statements: list[str] = []
    counterexample_found: int = 0  # 处于 DISPUTED 的规则数（反例触发过）
    regression_rate: float = 0.0  # 由演化轨道(T15)的 regression_violations 汇总，此处占位


def _op_ok(op: str, value: object, expected: object) -> bool:
    if op == ">=":
        return value >= expected
    if op == "==":
        return value == expected
    if op == "!=":
        return value != expected
    return False


def _claim_satisfies(claim: dict) -> bool:
    """结构化声明对照约束表 claim 键（V2 §6.3 机器可判）。

    两级匹配：(a) 同动作直配；(b) 嵌入参数——管线式 claim（如 export 前置
    convert）常把决定性参数编码进自身参数名（preceded_by_convert_dpi: 200），
    只要参数名含约束参数名且值满足算子即认可（真策略）。
    """
    action = claim.get("action")
    params = claim.get("params") or {}
    for constraint in HIDDEN_CONSTRAINTS:
        spec = constraint.get("claim")
        if not spec:
            continue
        param, op, expected = spec["param"], spec["op"], spec["value"]
        if spec["action"] == action and param in params:
            if _op_ok(op, params[param], expected):
                return True
            continue
        # 嵌入参数：参数名携带约束参数语义（如 *_dpi），跨动作认可真策略
        for name, value in params.items():
            if param in name and _op_ok(op, value, expected):
                return True
    return False


def statement_matches_truth(content: str | dict) -> bool:
    """陈述/结构化声明是否与 HIDDEN_CONSTRAINTS 一致（审计共用）。

    dict 且含 claim：以结构化声明为准（机器判定，正确文本不救错误参数）；
    否则退回 statement 文本关键词匹配（V1 语义）。
    """
    if isinstance(content, dict):
        claim = content.get("claim")
        if isinstance(claim, dict) and claim:
            return _claim_satisfies(claim)
        return _text_matches(str(content.get("statement", "")))
    return _text_matches(content)


def _text_matches(statement: str) -> bool:
    s = statement.lower()
    return any(all(k in s for k in keywords) for keywords in _TRUTH_KEYWORDS.values())


def make_gateway_reviewer(gateway: ModelGateway, model_id: str) -> Callable[[str], object]:
    """强模型复核器（T15）：LLM 复核陈述是否站得住，可推翻确定性匹配。

    返回 awaitable（audit_promoted 以 inspect.isawaitable 兼容同步 reviewer）。
    """

    async def reviewer(statement: str) -> bool:
        adapter = gateway.route({"inputs": [Modality.TEXT]})
        resp = await adapter.infer(
            InferRequest(
                model_id=adapter.capabilities().model_id,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an adversarial auditor for a document toolchain. "
                            "Decide whether the statement correctly describes a real "
                            "tool constraint. Answer strictly starting with YES or NO."
                        ),
                    },
                    {"role": "user", "content": statement},
                ],
            )
        )
        return resp.text.strip().upper().startswith("YES")

    return reviewer


async def audit_promoted(
    memory_store: MemoryStore,
    corpus=None,  # noqa: ARG001 — 保留签名对齐 spec（真值在常量表，语料本身不需要）
    reviewer: _REVIEWER = None,
) -> AuditReport:
    rules: list[MemoryRecord] = await memory_store.query(NS_RULES, {})
    promoted = [r for r in rules if r.verification is VerificationState.VERIFIED]

    wrong: list[str] = []
    for r in promoted:
        statement = str(r.content.get("statement", ""))
        ok = statement_matches_truth(r.content)  # V2：兼容结构化声明（claim 机器判定）
        if ok and reviewer is not None:
            verdict = reviewer(statement)  # 强模型复核可推翻确定性匹配
            if inspect.isawaitable(verdict):
                verdict = await verdict
            ok = verdict
        if not ok:
            wrong.append(statement)

    disputed = sum(1 for r in rules if r.verification is VerificationState.DISPUTED)
    total = len(promoted)
    return AuditReport(
        total=total,
        error_count=len(wrong),
        error_promotion_rate=(len(wrong) / total) if total else 0.0,
        wrong_statements=wrong,
        counterexample_found=disputed,
    )

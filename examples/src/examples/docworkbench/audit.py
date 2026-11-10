"""audit_promoted — 晋级记忆正确性审计（spec §7.2）。

真值来源：tools.HIDDEN_CONSTRAINTS（可审计常量表）+ 可注入的强模型复核。
错误经验晋级率是 V1 主指标之一：宁可少学，不可学错。
"""

from __future__ import annotations

from collections.abc import Callable

from agent_core.memory.record import MemoryRecord, VerificationState
from agent_core.memory.store import NS_RULES, MemoryStore
from pydantic import BaseModel

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


def _matches_truth(statement: str) -> bool:
    s = statement.lower()
    return any(all(k in s for k in keywords) for keywords in _TRUTH_KEYWORDS.values())


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
        ok = _matches_truth(statement)
        if ok and reviewer is not None:
            ok = reviewer(statement)  # 强模型复核可推翻确定性匹配
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

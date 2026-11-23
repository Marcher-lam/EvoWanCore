"""crew learning（V15-T1）——B 闭环记忆全链。

失败模式 → 祈使规则（MANDATORY: call skill BEFORE proceeding）→
MemoryStore（VERIFIED+ACTIVE）→ SimpleRetriever 检索 →
TokenBudgetAssembler 祈使渲染 → runtime 注入模型上下文。
与 docworkbench 的 B 闭环同机制（V4 验证：真 VLM 对祈使式遵循率高）。
"""

from __future__ import annotations

from pathlib import Path

from agent_core.memory.record import (
    LifecycleState,
    MemoryKind,
    MemoryRecord,
    VerificationState,
)
from agent_core.memory.store import NS_RULES, SqliteMemoryStore

_RULE_TEXT = {
    "date_format": (
        "日期含斜杠（如 2026/10/18）的票据，必须先调用 normalize_date "
        "归一化再提交字段"
    ),
    "currency_symbol": (
        "金额为带符号千分位串（如 ￥385,000）的票据，必须先调用 "
        "normalize_amount 归一化再提交字段"
    ),
    "tax_rate": (
        "税率为 3% 的硬件采购票（口径错），必须先调用 correct_tax_rate "
        "校正为 6% 再提交字段"
    ),
    "amount_words": (
        "金额为大写中文（如 叁万捌仟伍佰元整）的票据，必须先调用 "
        "normalize_amount_words 归一化再提交字段"
    ),
}

_SKILL_IDS = {
    "date_format": "normalize_date",
    "currency_symbol": "normalize_amount",
    "tax_rate": "correct_tax_rate",
    "amount_words": "normalize_amount_words",
}

# 祈使渲染需非空 params（平台 _render 契约）——占位引用票据字段
_SKILL_PARAMS = {
    "date_format": {"value": "payload.date"},
    "currency_symbol": {"value": "payload.amount_raw"},
    "tax_rate": {"value": "payload.tax_rate"},
    "amount_words": {"value": "payload.amount_words_raw"},
}

# 复合脏的"全调指令"规则：多脏票据不得中途提交（学习增益的载体）
_MULTI_DIRTY_RULE = (
    "invoice_extract 票据同时存在多种脏模式时，必须依次调用全部相关"
    "归一化技能后一次性提交——禁止半修提交"
)


def reflect_rules(failures: list[list[str]]) -> list[MemoryRecord]:
    """失败模式集合 → 去重规则集（每模式一条 + 多脏总则一条）。

    稳定 id（rule_{mode}）：同规则重反思 → put 走 revision 而非
    无限新增行（否则多 wave 后重复项挤占检索 limit 窗）。
    evidence_refs 附失败证据（crew demo 以失败标签为证——平台的
    verify_transition 全门禁在 docworkbench B 闭环走完整流程）。
    """
    seen: set[str] = set()
    for modes in failures:
        seen.update(modes)

    rules: list[MemoryRecord] = []
    for mode in sorted(seen):
        rules.append(MemoryRecord(
            id=f"rule_{mode}",
            kind=MemoryKind.RULE, namespace=NS_RULES,
            content={
                "statement": _RULE_TEXT[mode],
                "claim": {
                    "action": _SKILL_IDS[mode],
                    "params": _SKILL_PARAMS[mode],
                },
            },
            evidence_refs=[f"crew_failure:{mode}"],
            verification=VerificationState.VERIFIED,
            lifecycle=LifecycleState.ACTIVE,
        ))
    # 总则：单票据复合失败（len(modes)>=2）才沉淀——跨票据模式并集
    # 不构成"复合失败"证据。总则为 directive（无 claim.call——渲染为
    # 描述行，不得指示调用不存在的"all_normalizers"工具）
    if any(len(modes) >= 2 for modes in failures):
        rules.append(MemoryRecord(
            id="rule_multi_dirty_directive",
            kind=MemoryKind.RULE, namespace=NS_RULES,
            content={"statement": _MULTI_DIRTY_RULE},
            evidence_refs=["crew_failure:multi_dirty"],
            verification=VerificationState.VERIFIED,
            lifecycle=LifecycleState.ACTIVE,
        ))
    return rules


class CrewMemory:
    """crew 的规则记忆库（B 闭环存储侧）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.store = SqliteMemoryStore(db_path)

    async def write(self, rules: list[MemoryRecord]) -> None:
        for r in rules:
            await self.store.put(r)

    def retriever(self):
        from agent_core.memory.retrieve import SimpleRetriever

        return SimpleRetriever(self.store)

    def assembler(self):
        from agent_core.memory.retrieve import TokenBudgetAssembler

        return TokenBudgetAssembler()

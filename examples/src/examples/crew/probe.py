"""V18 反饥饿探索器——主动合成难例（counterexample 供给反射器）。

饥饿机制（V17 真跑发现"变强饿死学习"）：学习素材只来自 eval 批的
失败 → 批全过 = 反射器断粮 → 后续对残余难例无新技能 → 成功率回落。
prober 每 wave 在批解算后主动合成两类探针，不等失败上门：
- frontier：批分布外的脏模式（amount_words 大写金额）——世界比课程大，
  只有主动探索才会碰到"没学会的"
- boundary：已进化技能的 counterexample 值走端到端全链（区别于沙箱
  合同孤测——抓"技能在沙箱里对、agent 用不对"的真失败形态）

失败归因：探针失败由 prober 依合成真值打标签（prober 知道自己探的是
哪块领地）；grader 是字段级盲猜，会把大写金额误标成已进化的
currency_symbol——那是饥饿的第二形态（失败存在但被去重吞掉）。
"""

from __future__ import annotations

from dataclasses import dataclass

from examples.crew.domain import Invoice, dirty_payload

# frontier 议程：批分布外的脏模式（探索顺序即课程——按发现优先级排）
FRONTIER_MODES = ("amount_words",)

# frontier 探针票金额（覆盖 万段+段内零 两种形态）
_FRONTIER_AMOUNTS = (38500.0, 205.0)


@dataclass
class ProbeCase:
    """一个探针：合成票据 + 模型看到的脏视图 + 合成真值（归因口径）。"""

    inv: Invoice
    payload: dict
    mode: str  # 探的哪块领地（失败时覆盖字段级盲猜标签）
    origin: str  # frontier | boundary


def _mk(inv_id: str, amount: float, *, date: str = "2026-10-18") -> Invoice:
    return Invoice(
        invoice_id=inv_id, vendor=f"v-{inv_id}", amount=amount,
        date=date, tax_rate=0.06, category="hardware",
    )


def _unique(base: str, existing_ids: set[str]) -> str:
    inv_id = base
    n = 0
    while inv_id in existing_ids:
        n += 1
        inv_id = f"{base}-{n}"
    existing_ids.add(inv_id)
    return inv_id


def frontier_probes(
    *, evolved: set[str], existing_ids: set[str],
) -> list[ProbeCase]:
    """未进化的 frontier 领地 → 探针票（已进化领地不再重复探）。"""
    out: list[ProbeCase] = []
    for mode in FRONTIER_MODES:
        if mode in evolved:
            continue
        for i, amount in enumerate(_FRONTIER_AMOUNTS):
            inv = _mk(_unique(f"probe-f-{mode[:6]}-{i}", existing_ids),
                      amount)
            out.append(ProbeCase(
                inv, dirty_payload(inv, mode), mode, "frontier",
            ))
    return out


# 边界探针的 truth 构造：counterexample 值须从 truth 再生
# （dirty_payload 的生成函数确定性地把 truth 变成边界脏值）
_CE_TRUTH: dict[str, tuple[str | None, float | None]] = {
    "date_format": ("2028-02-29", None),  # ce_leap 闰年边界
    "currency_symbol": (None, 0.0),  # ce_zero 零额边界
    "tax_rate": (None, None),  # ce_anything（truth 无特殊要求）
    "amount_words": (None, 10.0),  # ce_ten 隐式壹边界（壹拾）
}


def boundary_probes(
    *, evolved: set[str], existing_ids: set[str],
) -> list[ProbeCase]:
    """已进化技能 → 各取一个 counterexample 值走端到端全链（持续供给）。

    每类每 wave 一票：技能在册后边界审计不中断——真模型世界里
    "技能存在但用不对"的失败（V17 wave3 currency 回落形态）由此浮出。
    """
    out: list[ProbeCase] = []
    for mode in sorted(evolved):
        date, amount = _CE_TRUTH.get(mode, (None, None))
        inv = _mk(
            _unique(f"probe-b-{mode[:4]}", existing_ids),
            amount if amount is not None else 250.0,
            date=date or "2026-10-18",
        )
        out.append(ProbeCase(
            inv, dirty_payload(inv, mode), mode, "boundary",
        ))
    return out


def synthesize_probes(
    *, evolved: set[str], existing_ids: set[str],
) -> list[ProbeCase]:
    """探索议程：先 frontier（找没学会的），再 boundary（验学会的边界）。"""
    return frontier_probes(
        evolved=evolved, existing_ids=existing_ids,
    ) + boundary_probes(evolved=evolved, existing_ids=existing_ids)

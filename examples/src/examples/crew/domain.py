"""crew 票据域（V14-T1）——Invoice + 脏模式注入 + 字段级评分器。

脏模式是"可学技能"的素材：每种模式对应一个归一化技能，grader 同时
给出错误标签（反射器据此产技能候选）。
"""

from __future__ import annotations

from pydantic import BaseModel

DIRTY_MODES = ("date_format", "tax_rate", "currency_symbol")


class Invoice(BaseModel):
    invoice_id: str
    vendor: str
    amount: float
    date: str  # 规范格式 YYYY-MM-DD
    tax_rate: float
    category: str
    amount_raw: str | None = None  # currency_symbol 脏模式的原始串


def dirty_payload(inv: Invoice, mode: str) -> dict:
    """生成模型看到的脏视图（truth 保持规范值——grader 永远对照规范）。"""
    if mode == "date_format":
        return inv.model_dump() | {"date": inv.date.replace("-", "/")}
    if mode == "tax_rate":
        return inv.model_dump() | {"tax_rate": 0.03}  # 口径错：小规模 3%
    if mode == "currency_symbol":
        # amount 本身变脏（带符号千分位串）——模型看到的才是真脏
        raw = f"￥{inv.amount:,.0f}"
        return inv.model_dump() | {"amount": raw, "amount_raw": raw}
    if mode == "amount_words":
        # 大写中文金额（发票标准写法）——prober 的 frontier 探针领域：
        # eval 批分布之外，只有主动探索才会碰到（V18 反饥饿）
        words = to_chinese_amount(inv.amount)
        return inv.model_dump() | {
            "amount": words, "amount_words_raw": words,
        }
    raise ValueError(f"未知脏模式: {mode}")


_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS = {1000: "仟", 100: "佰", 10: "拾", 1: ""}


def _four_digits(v: int) -> str:
    """0..9999 → 大写段（段内零补'零'，如 0205→零贰零伍 由调用侧拼）。"""
    s = ""
    zero_pending = False
    for u in (1000, 100, 10, 1):
        d = v // u % 10
        if d:
            if zero_pending:
                s += "零"
                zero_pending = False
            s += _CN_DIGITS[d] + _CN_UNITS[u]
        elif s:
            zero_pending = True
    return s


def to_chinese_amount(amount: float) -> str:
    """数值 → 大写中文金额（整数额度，拾亿内）——amount_words 的生成侧。

    标准写法：高位段非零且低位段 0<rest<1000 时补段间零
    （10005 → 壹万零伍，终评审 P2-1）。
    """
    n = int(amount)
    if n == 0:
        return "零元整"
    parts: list[str] = []
    wan, rest = divmod(n, 10000)
    yi, wan = divmod(wan, 10000)
    if yi:
        parts.append(_four_digits(yi) + "亿")
    if wan:
        parts.append(_four_digits(wan) + "万")
    if rest or not parts:
        low = _four_digits(rest)
        if parts and 0 < rest < 1000:
            low = "零" + low
        parts.append(low)
    return "".join(parts) + "元整"


def grader(
    extracted: dict, truth: Invoice
) -> tuple[bool, list[str]]:
    """字段级对照。返回 (pass, 错误模式标签列表)。

    真理值 = 规范语义（amount 数值 / date ISO / tax_rate 正确口径）——
    脏票据归一化后的抽取应与规范值一致（技能价值定义）。
    """
    errors: list[str] = []
    if not _num_eq(extracted.get("amount"), truth.amount):
        errors.append("currency_symbol")
    if str(extracted.get("date", "")) != truth.date:
        errors.append("date_format")
    if not _num_eq(extracted.get("tax_rate"), truth.tax_rate):
        errors.append("tax_rate")
    return (not errors, errors)


def _num_eq(a: object, b: float) -> bool:
    try:
        return abs(float(a) - b) < 1e-9  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def dirty_payloads(inv: Invoice, modes: list[str]) -> dict:
    """复合脏：多模式叠加（单模式场景仍用 dirty_payload）。"""
    payload = inv.model_dump()
    for mode in modes:
        d = dirty_payload(inv, mode)
        for k, v in d.items():
            if k == "amount" and isinstance(v, str) or k != "amount":
                payload[k] = v
    # date 模式单独处理（dirty_payload 会覆写整个 date）
    if "date_format" in modes:
        payload["date"] = inv.date.replace("-", "/")
    if "currency_symbol" in modes:
        raw = f"￥{inv.amount:,.0f}"
        payload["amount"] = raw
        payload["amount_raw"] = raw
    return payload

"""V14-T1 crew domain：票据 + 脏模式注入 + 字段级评分器。"""

from examples.crew.domain import DIRTY_MODES, Invoice, dirty_payload, grader


def _clean() -> Invoice:
    return Invoice(
        invoice_id="inv-001",
        vendor="杭州云启科技",
        amount=385000.0,
        date="2026-10-18",
        tax_rate=0.06,
        category="hardware",
    )


def test_dirty_modes_registry() -> None:
    assert set(DIRTY_MODES) == {"date_format", "tax_rate", "currency_symbol"}


def test_dirty_payload_each_mode() -> None:
    payload = dirty_payload(_clean(), "date_format")
    assert payload["date"] == "2026/10/18", "斜杠日期"
    assert payload["amount"] == 385000.0

    payload = dirty_payload(_clean(), "tax_rate")
    assert payload["tax_rate"] == 0.03, "税率口径错（3% 应为 6%）"

    payload = dirty_payload(_clean(), "currency_symbol")
    assert payload["amount_raw"] == "￥385,000", "带符号千分位原始串"


def test_grader_clean_passes() -> None:
    ok, errors = grader(
        extracted={"amount": 385000.0, "date": "2026-10-18", "tax_rate": 0.06},
        truth=_clean(),
    )
    assert ok and errors == []


def test_grader_flags_each_dirty_mode() -> None:
    # date：脏值直交 → 错 + 标签
    ok, errors = grader(
        extracted={"amount": 385000.0, "date": "2026/10/18", "tax_rate": 0.06},
        truth=_clean(),
    )
    assert not ok and errors == ["date_format"]

    ok, errors = grader(
        extracted={"amount": 385000.0, "date": "2026-10-18", "tax_rate": 0.03},
        truth=_clean(),
    )
    assert not ok and errors == ["tax_rate"]

    # currency：模型交了原始串（未归一化）→ 错
    ok, errors = grader(
        extracted={"amount": "￥385,000", "date": "2026-10-18", "tax_rate": 0.06},
        truth=_clean(),
    )
    assert not ok and errors == ["currency_symbol"]


def test_grader_normalized_value_passes_dirty_truth() -> None:
    """脏 payload + 归一化后的抽取值 → 过（技能的价值定义）。"""
    ok, errors = grader(
        extracted={"amount": 385000.0, "date": "2026-10-18", "tax_rate": 0.06},
        truth=_clean(),
    )
    assert ok and errors == [], "归一化后应与真理值一致"

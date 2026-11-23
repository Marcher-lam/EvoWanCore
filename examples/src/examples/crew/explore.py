"""crew 探索全量（V15-T3）——curriculum / gap_driven / counterexample。

- curriculum：wave1 干净+单脏；wave2+ 注入复合脏（难度递进）
- gap_driven：失败模式 → 同模式新票注入下轮批
- counterexample：per-mode 边界值（闰年/跨年单数字/零额）追加进技能
  合同——边界作弊（训练样本全对、边界错）被锁
"""

from __future__ import annotations

import random

from examples.crew.domain import Invoice, dirty_payload, dirty_payloads

_SINGLE_MODES = ["date_format", "currency_symbol", "tax_rate"]
_MULTI_COMBOS = [["date_format", "currency_symbol"],
                 ["date_format", "tax_rate"],
                 ["currency_symbol", "tax_rate"]]

# 边界反例（进技能合同 expected_outputs）
_COUNTEREXAMPLES: dict[str, dict[str, dict]] = {
    "date_format": {
        "ce_leap": {"args": {"value": "2028/2/29"},
                    "expect_stdout_contains": '"normalized": "2028-02-29"'},
        "ce_single_digit": {
            "args": {"value": "2026/1/1"},
            "expect_stdout_contains": '"normalized": "2026-01-01"'},
    },
    "currency_symbol": {
        "ce_zero": {"args": {"value": "￥0"},
                    "expect_stdout_contains": '"normalized": 0.0'},
        "ce_small": {"args": {"value": "￥7"},
                     "expect_stdout_contains": '"normalized": 7.0'},
    },
    "tax_rate": {
        "ce_anything": {"args": {"value": "0.01"},
                        "expect_stdout_contains": '"normalized": 0.06'},
    },
    # V18 frontier 反例：隐式壹（拾=10）与零额——锁住解析器边界作弊
    "amount_words": {
        "ce_ten": {"args": {"value": "拾元整"},
                   "expect_stdout_contains": '"normalized": 10.0'},
        "ce_zero": {"args": {"value": "零元整"},
                    "expect_stdout_contains": '"normalized": 0.0'},
    },
}


def counterexample_cases(mode: str) -> dict[str, dict]:
    return dict(_COUNTEREXAMPLES.get(mode, {}))


def _mk(inv_id: str, amount: float) -> Invoice:
    return Invoice(
        invoice_id=inv_id, vendor=f"v-{inv_id}", amount=amount,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )


def curriculum_batches(
    *, total_waves: int, seed: int = 0, batch_size: int = 6,
) -> list[list[tuple[Invoice, dict]]]:
    """课程化批序列：wave1 单脏入门，wave2+ 混入复合脏（可复现随机）。"""
    rng = random.Random(seed)
    batches: list[list[tuple[Invoice, dict]]] = []
    counter = 0
    for w in range(1, total_waves + 1):
        batch: list[tuple[Invoice, dict]] = []
        for j in range(batch_size):
            counter += 1
            inv = _mk(f"c-{w}-{j}", 100.0 * counter)
            if j == batch_size - 1:
                payload: dict = inv.model_dump()  # 每批留一张干净
            elif w == 1:
                mode = _SINGLE_MODES[j % len(_SINGLE_MODES)]
                payload = dirty_payload(inv, mode)
            else:
                # seed 真用：可复现地打乱组合选择（难度仍递进——全为复合）
                combo = _MULTI_COMBOS[rng.randrange(len(_MULTI_COMBOS))]
                payload = dirty_payloads(inv, combo)
            batch.append((inv, payload))
        batches.append(batch)
    return batches


def expand_with_gaps(
    base: list[tuple[Invoice, dict]],
    failures_by_mode: dict[str, int],
    *,
    existing_ids: set[str],
) -> list[tuple[Invoice, dict]]:
    """gap_driven：每个失败模式生成同模式新票追加（避开已有 id）。"""
    out = list(base)
    counter = 0
    for mode, count in sorted(failures_by_mode.items()):
        for _ in range(count):
            counter += 1
            inv_id = f"gap-{mode[:4]}-{counter}"
            while inv_id in existing_ids:
                counter += 1
                inv_id = f"gap-{mode[:4]}-{counter}"
            inv = _mk(inv_id, 250.0 + counter)
            out.append((inv, dirty_payload(inv, mode)))
            existing_ids.add(inv_id)
    return out

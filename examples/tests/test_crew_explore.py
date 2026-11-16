"""V15-T3 crew 探索全量：curriculum 递进 / gap_driven 注入 / counterexample 合同。"""

from pathlib import Path

from examples.crew.domain import Invoice


def _inv(i: int, amount: float = 300.0) -> Invoice:
    return Invoice(
        invoice_id=f"x-{i}", vendor=f"v{i}", amount=amount,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )


def test_curriculum_progressive_difficulty() -> None:
    """wave1 只干净+单脏；wave2 起注入复合脏（难度递进）。"""
    from examples.crew.explore import curriculum_batches

    batches = curriculum_batches(total_waves=3, seed=42)
    assert len(batches) == 3
    w1_payloads = [p.model_dump() if hasattr(p, "model_dump") else p
                   for _, p in batches[0]]
    assert all("/" not in str(p.get("date")) or p.get("amount_raw") is None
               for p in w1_payloads), "wave1 不该有复合脏"
    # wave2+ 含复合脏（date+currency 同时）
    w2 = batches[1]
    has_multi = any(
        "/" in str(p.get("date", "")) and p.get("amount_raw")
        for _, p in w2
    )
    assert has_multi, "wave2 应注入复合脏"


def test_gap_driven_injects_new_tickets() -> None:
    """失败模式 → 同模式新票注入下轮批（gap 扩充）。"""
    from examples.crew.explore import expand_with_gaps

    base = [(_inv(1), _inv(1).model_dump())]
    gaps = {"date_format": 2, "tax_rate": 1}
    expanded = expand_with_gaps(base, gaps, existing_ids={"x-1"})
    ids = {inv.invoice_id for inv, _ in expanded}
    assert "x-1" in ids
    added = [p for inv, p in expanded if inv.invoice_id != "x-1"]
    assert len(added) == 3, "2 date + 1 tax 新票"
    assert all("/" in str(p["date"]) for p in added[:2]), "date gap 新票"


def test_counterexample_boundary_cases_in_contract() -> None:
    """反例生成：per-mode 边界值（闰年/零额）追加进技能合同。"""
    from examples.crew.explore import counterexample_cases

    cases = counterexample_cases("date_format")
    assert any("2028/2/29" in c["args"]["value"] for c in cases.values()), \
        "闰年边界必须在合同"
    assert any(c["args"]["value"] == "2026/1/1" for c in cases.values())


async def test_counterexample_locks_boundary_cheater(tmp_path: Path) -> None:
    """边界作弊技能（训练样本全对、边界值错）被反例增强合同拒。"""
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.solver import make_solver_env

    env = make_solver_env(tmp_path)
    ok = await register_skill_for_mode(
        env, "date_format", workspace=tmp_path / "ws",
        scripts={"main.py": (
            "import json, sys\n"
            "args = json.loads(sys.argv[1])\n"
            "v = args.get('value', '')\n"
            "# 对常见样本正确，边界（闰年/跨年单数字）全 BROKEN\n"
            "known = {'2026/10/18', '2025/1/5', '2026/12/31'}\n"
            "out = v if v in known else 'BROKEN'\n"
            "print(json.dumps({'normalized': out}))\n"
        )},
    )
    assert ok is False, "反例增强合同必须锁住边界作弊"

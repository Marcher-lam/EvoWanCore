"""V18 反饥饿探索闭环：主动合成难例（counterexample）持续供料反射器。

饥饿机制（V17 真跑发现"变强饿死学习"）：学习素材只来自 eval 批失败 →
批全过 = 反射器断粮。本版：prober 在批解算后主动合成 frontier 探针
（批分布外的脏模式 amount_words——发票大写金额）+ 边界探针（已进化
技能的 counterexample 值走端到端全链），失败并流反射器/进化器——
系统主动去找"没学会的"，不等失败上门。
"""

from pathlib import Path

from conftest import mixed_batch


def _clean_batch(n: int = 6) -> list:
    """全干净批：批层永不失败（饥饿复现的前提）。"""
    return mixed_batch([None] * n)


async def test_amount_words_skill_real_contract_and_e2e(tmp_path: Path) -> None:
    """frontier 技能 normalize_amount_words：真解析器过合同门禁 + 端到端可用。"""
    from examples.crew.domain import Invoice, dirty_payload
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.solver import make_solver_env, solve_invoice

    env = make_solver_env(tmp_path)
    assert await register_skill_for_mode(
        env, "amount_words", workspace=tmp_path / "ws", generation=1,
    ), "中文大写金额解析技能必须过真合同（含拾/零边界）"

    inv = Invoice(
        invoice_id="aw-1", vendor="商家", amount=38500.0,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )
    payload = dirty_payload(inv, "amount_words")
    r = await solve_invoice(env, inv, payload)
    assert r.passed, "技能就位后大写金额票据必须端到端通过"
    assert r.steps == 2, f"调技能+提交即直通，实际 {r.steps} 步"


async def test_starvation_without_probe(tmp_path: Path) -> None:
    """饥饿复现（对照组）：全净批 3-wave——0 技能 0 规则，学习全程停摆。"""
    from examples.crew.crew import make_crew
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env

    mem = CrewMemory(tmp_path / "mem.db")
    env = make_solver_env(tmp_path, memory=mem)
    crew = make_crew(env, workspace=tmp_path / "ws", memory=mem)
    metrics = await crew.run_waves(_clean_batch(), n_waves=3)

    assert all(m.success_rate == 1.0 for m in metrics)
    assert all(m.skills == 0 for m in metrics), "批层无失败 → 不该有技能"
    records = await mem.store.query("rules", {})
    assert records == [], f"无失败即无学习素材，记忆必须为空: {records}"


async def test_probe_feeds_learning_despite_full_batch_pass(
    tmp_path: Path,
) -> None:
    """反饥饿（实验组）：批全过，prober 供料 → 技能+规则增长+当 wave 自愈。"""
    from examples.crew.crew import make_crew
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env

    mem = CrewMemory(tmp_path / "mem.db")
    env = make_solver_env(tmp_path, memory=mem)
    crew = make_crew(env, workspace=tmp_path / "ws", memory=mem)
    metrics = await crew.run_waves(_clean_batch(), n_waves=3, probe=True)

    # 批层口径零污染：成功率仍 1.0，total 仍=批大小
    assert all(m.success_rate == 1.0 and m.total == 6 for m in metrics)

    w1 = metrics[0]
    # 探针失败 = 合成真值标签（非 grader 字段盲猜的 currency_symbol）
    assert w1.probe_failures_by_mode.get("amount_words", 0) >= 1, (
        f"frontier 探针必须失败且正确归因: {w1.probe_failures_by_mode}"
    )
    assert "currency_symbol" not in w1.probe_failures_by_mode, (
        "大写金额失败不得误标为已进化的 currency_symbol（饥饿第二形态）"
    )
    # 同 wave 自愈：进化出技能后探针重派救回
    assert w1.probe_recovered >= 1, "进化后探针必须能救回（闭环验证）"

    # wave2 起技能在册 + 规则沉淀（学习未断粮）
    assert metrics[1].skills >= 1
    records = await mem.store.query("rules", {})
    assert any(r.id == "rule_amount_words" for r in records), (
        "探针失败必须沉淀出祈使规则（B 闭环持续供料的证据）"
    )

    # 边界探针持续供给：wave2 起对已进化技能跑端到端边界审计（全通过）
    assert metrics[1].probe_total >= 1, \
        "frontier 学会后边界审计不得中断（持续供给）"
    assert metrics[1].probe_failures_by_mode == {}, (
        f"技能就位后边界探针应端到端通过: "
        f"{metrics[1].probe_failures_by_mode}"
    )


async def test_boundary_probes_audit_all_evolved_skills(
    tmp_path: Path,
) -> None:
    """混合批（经典三模式全进化）+ probe：边界探针覆盖全部已进化技能。"""
    from examples.crew.crew import make_crew
    from examples.crew.solver import make_solver_env

    batch = mixed_batch(
        ["date_format", "currency_symbol", "tax_rate",
         "date_format", None, "currency_symbol"],
    )
    env = make_solver_env(tmp_path)
    crew = make_crew(env, workspace=tmp_path / "ws")
    metrics = await crew.run_waves(batch, n_waves=3, probe=True)

    # wave1：批失败（3 经典模式）+ frontier 探针失败（amount_words）
    # → 4 技能全进化；探针同 wave 自愈
    assert metrics[0].probe_recovered >= 2, "frontier 探针必须当 wave 救回"
    # wave2：边界探针对 4 类已进化技能各一票，端到端全通过
    assert metrics[1].probe_total >= 4, (
        f"4 类技能在册 → 边界探针至少 4 票: {metrics[1].probe_total}"
    )
    assert metrics[1].probe_failures_by_mode == {}, (
        f"边界审计失败（技能在沙箱对但 agent 用不对的形态）: "
        f"{metrics[1].probe_failures_by_mode}"
    )
    # 批指标与非 probe 版语义一致：wave3 满分、技能 4 个
    assert metrics[2].success_rate == 1.0
    assert metrics[2].skills >= 4

"""V15-T5 e2e：crew 四能力全景——一条链同时验证学习/探索/编排/进化。"""

from pathlib import Path

from examples.crew.domain import Invoice, dirty_payload

from conftest import assert_platform_clean


def _inv(i: str, amount: float) -> Invoice:
    return Invoice(
        invoice_id=i, vendor="云启", amount=amount, date="2026-10-18",
        tax_rate=0.06, category="hardware",
    )


async def test_crew_four_capabilities_full_chain(tmp_path: Path) -> None:
    """四能力一条链：curriculum 探索 → 流水线编排 → 失败学习记忆 +
    技能进化 → 下轮变强（成功率/步数/被拒三指标）。"""
    from examples.crew.crew import make_crew
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env

    mem = CrewMemory(tmp_path / "crew_mem.db")
    env = make_solver_env(tmp_path, memory=mem)
    crew = make_crew(env, workspace=tmp_path / "ws", memory=mem)

    # wave1 批：单脏混合（curriculum 的入门难度）
    batch = []
    for i, mode in enumerate(
        ["date_format", "currency_symbol", "tax_rate",
         "date_format", None, "currency_symbol"],
    ):
        inv = _inv(f"f-{i}", 100.0 * (i + 1))
        batch.append((inv, dirty_payload(inv, mode) if mode
                      else inv.model_dump()))

    metrics = await crew.run_waves(
        batch, n_waves=3, pipeline=True, curriculum=True,
    )

    # 探索：wave1 发现全部 gap；curriculum 使 wave2+ 有复合脏新票
    assert set(metrics[0].failures_by_mode) == {
        "date_format", "currency_symbol", "tax_rate"}
    # 学习+进化：wave2 起技能与记忆双双就位 → 满分
    assert metrics[0].skills == 0
    assert metrics[0].success_rate == 1 / 6
    assert metrics[2].success_rate == 1.0
    assert metrics[2].skills == 3
    # 学习增益（弯路消失）：wave3 无被拒提交
    assert metrics[2].total_rejected == 0, \
        f"wave3 不得再有半修提交: {metrics[2]}"
    # 记忆真的写入了（B 闭环存储侧证据）
    records = await mem.store.query("rules", {})
    assert any("normalize_date" in str(r.content) for r in records), \
        "失败驱动的祈使规则必须已沉淀"
    # 编排：全链走三阶段流水线（stages 由 solve_pipeline 记录）
    probe = await crew.solve_pipeline(
        _inv("probe", 150.0), _inv("probe", 150.0).model_dump(),
    )
    assert probe.stages == ["classify", "extract", "audit"]


def test_crew_four_capabilities_platform_clean() -> None:
    """平台零侵入红线（V13 原则）。"""
    assert_platform_clean(Path(__file__).resolve().parents[2], "crew")

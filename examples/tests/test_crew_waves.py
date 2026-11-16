"""V14-T4 crew wave 循环：失败驱动进化 → wave 级指标（成功率↑ 步数↓）。"""

from pathlib import Path

from conftest import mixed_batch


def _batch(n: int = 6) -> list:
    """6 票混合批：2 date 脏 / 2 currency 脏 / 1 tax 脏 / 1 干净。"""
    modes_full: list[str | None] = [
        "date_format", "currency_symbol", "date_format",
        "currency_symbol", "tax_rate", None,
    ]
    return mixed_batch(modes_full[:n])


async def test_crew_waves_get_stronger(tmp_path: Path) -> None:
    """3 wave：成功率 0.5→...→1.0；步数下降；技能数增长（可量化变强）。"""
    from examples.crew.crew import make_crew
    from examples.crew.solver import make_solver_env

    env = make_solver_env(tmp_path)
    crew = make_crew(env, workspace=tmp_path / "ws")
    metrics = await crew.run_waves(_batch(), n_waves=3)

    assert len(metrics) == 3
    # wave1：无技能——脏票据全跪（5/6 失败 → 成功率 1/6）
    assert metrics[0].success_rate < 0.5, f"wave1 应大面积失败: {metrics[0]}"
    # wave3：技能齐——全部通过
    assert metrics[0].skills == 0
    assert metrics[2].success_rate == 1.0, f"wave3 应满分: {metrics[2]}"
    assert metrics[2].skills >= 3, "三种脏模式技能都应进化出"
    # 步数：wave3 平均步数显著低于 wave1
    assert metrics[2].avg_steps < metrics[0].avg_steps, (
        f"变强必须省步数: w1={metrics[0].avg_steps} w3={metrics[2].avg_steps}"
    )


async def test_crew_wave_report(tmp_path: Path) -> None:
    """wave 指标含失败明细（探索 gap 可见性）。"""
    from examples.crew.crew import make_crew
    from examples.crew.solver import make_solver_env

    env = make_solver_env(tmp_path)
    crew = make_crew(env, workspace=tmp_path / "ws")
    metrics = await crew.run_waves(_batch(3), n_waves=1)
    m = metrics[0]
    assert set(m.failures_by_mode) <= {
        "date_format", "currency_symbol", "tax_rate"}
    total = m.total
    assert total == 3

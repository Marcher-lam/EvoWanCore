"""V14-T5 crew e2e：三角色能力选人 + 3 wave 全链变强 + 平台零侵入。"""

from pathlib import Path

from examples.crew.crew import crew_agents as _agents


class _T:
    def __init__(self, kind: str):
        self.kind = kind


def test_role_selection_by_capability() -> None:
    """编排选人：任务 kind → 对应角色 agent（classify/extract/audit）。"""
    from agent_core.orchestration import CapabilityCompositionPolicy

    policy = CapabilityCompositionPolicy()
    agents = _agents()
    assert policy.select_participants(_T("classify"), agents) == ["classifier"]
    assert policy.select_participants(
        _T("invoice_extract"), agents) == ["extractor"]
    assert policy.select_participants(_T("audit"), agents) == ["auditor"]
    assert policy.select_participants(_T("unknown_kind"), agents) == []


async def test_crew_full_chain_gets_stronger(tmp_path: Path) -> None:
    """e2e：混合票据批 3 wave——探索发现 3 种 gap、自进化 3 技能、变强。"""
    from examples.crew.crew import make_crew
    from examples.crew.solver import make_solver_env

    from conftest import mixed_batch

    batch = mixed_batch([
        "date_format", "currency_symbol", "tax_rate",
        "date_format", None, "currency_symbol",
    ])

    env = make_solver_env(tmp_path)
    crew = make_crew(env, workspace=tmp_path / "ws")
    metrics = await crew.run_waves(batch, n_waves=3)

    # 探索：wave1 暴露全部 3 种 gap
    assert set(metrics[0].failures_by_mode) == {
        "date_format", "currency_symbol", "tax_rate"}, \
        f"探索应发现全部脏模式: {metrics[0].failures_by_mode}"
    # 自进化：3 技能（真门禁后注册）
    assert metrics[2].skills == 3
    # 变强：成功率与步数双指标
    assert metrics[0].success_rate == 1 / 6  # 仅干净票据过
    assert metrics[2].success_rate == 1.0
    assert metrics[2].avg_steps < metrics[0].avg_steps
    # 自愈：wave1 进化出技能后失败重派立即救回（H3 真编排路径）
    assert metrics[0].recovered == 5, (
        f"wave1 五张脏票应在进化后重派自愈: {metrics[0]}"
    )
    assert metrics[2].recovered == 0, "满分 wave 无失败可自愈"


def test_crew_does_not_touch_platform() -> None:
    """平台零侵入红线（V13 原则，共享 conftest 实现）。"""
    from conftest import assert_platform_clean

    assert_platform_clean(Path(__file__).resolve().parents[2], "crew")

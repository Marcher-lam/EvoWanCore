"""V15-T4 编排深化：classify→extract→audit 三阶段流水线 + 大额打回。"""

from pathlib import Path

from examples.crew.domain import Invoice


def _inv(i: str, amount: float) -> Invoice:
    return Invoice(
        invoice_id=i, vendor="云启", amount=amount, date="2026-10-18",
        tax_rate=0.06, category="hardware",
    )


async def test_pipeline_classify_extract_audit(tmp_path: Path) -> None:
    """小额干净票走完三阶段全通过（三角色各真执行）。"""
    from examples.crew.crew import make_crew
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env

    mem = CrewMemory(tmp_path / "mem.db")
    env = make_solver_env(tmp_path, memory=mem)
    crew = make_crew(env, workspace=tmp_path / "ws")
    inv = _inv("p-1", 200.0)
    result = await crew.solve_pipeline(inv, inv.model_dump())
    assert result.passed
    assert result.stages == ["classify", "extract", "audit"], \
        f"三阶段都必须真跑: {result.stages}"
    assert result.needs_review is False, "小额票不触发复核"


async def test_audit_flags_large_amount(tmp_path: Path) -> None:
    """大额票（≥复核阈值）被 audit 打回 needs_review。"""
    from examples.crew.crew import make_crew
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env

    mem = CrewMemory(tmp_path / "mem.db")
    env = make_solver_env(tmp_path, memory=mem)
    crew = make_crew(env, workspace=tmp_path / "ws")
    inv = _inv("p-2", 5000.0)
    result = await crew.solve_pipeline(inv, inv.model_dump())
    assert result.needs_review is True, "大额票必须被打回复核"
    assert "audit" in result.stages


async def test_dirty_ticket_pipeline_with_skills(tmp_path: Path) -> None:
    """脏票流水线：技能就位后三阶段直通（探索/进化成果作用于编排）。"""
    from examples.crew.crew import make_crew
    from examples.crew.domain import dirty_payload
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.learning import CrewMemory, reflect_rules
    from examples.crew.solver import make_solver_env

    mem = CrewMemory(tmp_path / "mem.db")
    await mem.write(reflect_rules([["date_format"]]))
    env = make_solver_env(tmp_path, memory=mem)
    for m in ("date_format",):
        assert await register_skill_for_mode(
            env, m, workspace=tmp_path / "ws")
    crew = make_crew(env, workspace=tmp_path / "ws")
    inv = _inv("p-3", 300.0)
    result = await crew.solve_pipeline(inv, dirty_payload(inv, "date_format"))
    assert result.passed, f"技能+记忆下脏票应直通: {result.error_tags}"

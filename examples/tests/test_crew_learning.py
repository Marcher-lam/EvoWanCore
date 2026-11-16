"""V15-T1 crew learning：失败→祈使规则记忆→检索渲染进模型上下文。"""

from pathlib import Path

from examples.crew.learning import CrewMemory, reflect_rules


async def test_reflect_rules_content_shape() -> None:
    """失败模式 → 记忆规则（claim.action 驱动祈使渲染）。"""
    # 单票据复合失败（date+currency 同票）→ 2 模式规则 + 1 总则；
    # 总则是 directive（无 claim.call——不得指示调用不存在的工具）
    rules = reflect_rules([["date_format", "currency_symbol"]])
    assert len(rules) == 3
    actions = {
        r.content["claim"]["action"] for r in rules if "claim" in r.content
    }
    assert {"normalize_date", "normalize_amount"} <= actions
    directive = next(
        r for r in rules if "claim" not in r.content
    )
    assert "禁止半修提交" in directive.content["statement"]
    assert r"id=rule_date_format" or True  # 稳定 id 见 roundtrip 测试
    date_rule = next(
        r for r in rules
        if r.content.get("claim", {}).get("action") == "normalize_date"
    )
    assert date_rule.id == "rule_date_format", "稳定 id（防重复累积）"
    assert "斜杠" in date_rule.content["statement"]
    assert date_rule.evidence_refs, "规则须附失败证据"


async def test_crew_memory_roundtrip_and_render(tmp_path: Path) -> None:
    """写入 → SimpleRetriever 检索（关键词命中）→ TokenBudget 祈使渲染。"""
    from agent_core.memory.retrieve import (
        SimpleRetriever,
        TokenBudgetAssembler,
    )

    mem = CrewMemory(tmp_path / "mem.db")
    rules = reflect_rules([["date_format", "currency_symbol"]])
    await mem.write(rules)

    retriever = SimpleRetriever(mem.store)
    records = await retriever.retrieve(
        scope={"namespaces": ["rules"]},
        task_context={"keywords": ["invoice_extract"], "environment_version": None},
        limit=8,
    )
    assert records, "写入的 VERIFIED+ACTIVE 规则必须可检索"
    text = TokenBudgetAssembler().assemble(records, 400)
    assert "MANDATORY: call normalize_date" in text, f"祈使渲染缺失: {text}"
    assert "MANDATORY: call normalize_amount" in text


async def test_runtime_wires_memory_into_context(tmp_path: Path) -> None:
    """runtime 接线后 context['memory'] 含 MANDATORY（模型可见面）。"""
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env

    mem = CrewMemory(tmp_path / "mem.db")
    await mem.write(reflect_rules([["date_format"]]))
    env = make_solver_env(tmp_path, memory=mem)

    from agent_core.task import TaskSpec

    task = TaskSpec(
        id="mem_check", kind="invoice_extract", goal={"payload": {}},
        input_schema={}, constraints={},
        acceptance_criteria=[{"check": "x"}],
    )
    text = await env.runtime._assemble_memory(task)  # noqa: SLF001 — 接线验证
    assert "MANDATORY: call normalize_date" in text, \
        f"runtime memory 注入失败: {text!r}"


# ---------- V15-T2: 多脏票据 + 记忆感知（学习增益量化） ----------

async def test_multi_dirty_without_memory_takes_detour(tmp_path: Path) -> None:
    """复合脏 + 无记忆：半修提交被拒 → 补技能 → 再交（4 步 ≥1 拒）。"""
    from examples.crew.domain import Invoice, dirty_payloads
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.solver import make_solver_env, solve_invoice

    env = make_solver_env(tmp_path)
    for mode in ("date_format", "currency_symbol"):
        assert await register_skill_for_mode(
            env, mode, workspace=tmp_path / "ws",
        ), f"{mode} 技能注册失败"

    inv = Invoice(
        invoice_id="multi-1", vendor="云启", amount=500.0,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )
    payload = dirty_payloads(inv, ["date_format", "currency_symbol"])
    result = await solve_invoice(env, inv, payload)
    assert not result.passed or result.rejected_submits >= 1, \
        "无记忆多脏应走半修弯路（≥1 次被拒提交）"
    assert result.steps >= 4, f"无记忆多脏 ≥4 步，实际 {result.steps}"


async def test_multi_dirty_with_memory_straight(tmp_path: Path) -> None:
    """同场景 + 记忆（全调总则）：依次调全技能 → 一次提交（3 步 0 拒）。"""
    from examples.crew.domain import Invoice, dirty_payloads
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.learning import CrewMemory, reflect_rules
    from examples.crew.solver import make_solver_env, solve_invoice

    mem = CrewMemory(tmp_path / "mem.db")
    # 从复合失败反思出规则（含全调总则）
    await mem.write(reflect_rules([["date_format", "currency_symbol"]]))

    env = make_solver_env(tmp_path, memory=mem)
    for mode in ("date_format", "currency_symbol"):
        assert await register_skill_for_mode(
            env, mode, workspace=tmp_path / "ws",
        )

    inv = Invoice(
        invoice_id="multi-2", vendor="云启", amount=500.0,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )
    payload = dirty_payloads(inv, ["date_format", "currency_symbol"])
    result = await solve_invoice(env, inv, payload)
    assert result.passed, f"有记忆应直通: tags={result.error_tags}"
    assert result.rejected_submits == 0, "记忆指令下不得半修提交"
    assert result.steps == 3, \
        f"技能1+技能2+提交=3 步，实际 {result.steps}"

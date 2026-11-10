"""T13 确定性召回 + token 预算上下文组装。"""

from pathlib import Path

import pytest
from agent_core.memory.record import MemoryKind, MemoryRecord, VerificationState, verify_transition
from agent_core.memory.retrieve import SimpleRetriever, TokenBudgetAssembler
from agent_core.memory.store import NS_RULES, SqliteMemoryStore


@pytest.fixture
async def store(tmp_path: Path) -> SqliteMemoryStore:
    s = SqliteMemoryStore(tmp_path / "mem.db")
    return s


def _rule(statement: str, **over) -> MemoryRecord:
    base = dict(
        kind=MemoryKind.RULE,
        namespace=NS_RULES,
        content={"statement": statement},
        evidence_refs=["evi_x"],
        applicability={"app": "workbench"},
        environment_version="1.0",
    )
    base.update(over)
    return MemoryRecord(**base)


async def _verified(store: SqliteMemoryStore, rec: MemoryRecord) -> MemoryRecord:
    await store.put(rec)
    current = await store.get(rec.id)
    verified = verify_transition(current, VerificationState.VERIFIED, evidence_ok=True)
    await store.put(verified)
    return await store.get(rec.id)


async def test_stale_environment_version_excluded(store: SqliteMemoryStore) -> None:
    # V2 §6.4 后默认排除 CANDIDATE——本测试聚焦环境版本过滤，记录取 VERIFIED
    await store.put(_rule("新版本规则", environment_version="1.0",
                          verification=VerificationState.VERIFIED))
    await store.put(_rule("旧版本规则", environment_version="0.9",
                          verification=VerificationState.VERIFIED))

    retriever = SimpleRetriever(store)
    got = await retriever.retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": "1.0", "keywords": []},
        limit=10,
    )
    assert [r.content["statement"] for r in got] == ["新版本规则"]


async def test_candidate_excluded_by_default(store: SqliteMemoryStore) -> None:
    """V2 §6.4：CANDIDATE 默认不进执行上下文（关候选泄漏）；显式 include 才可见。"""
    verified = await _verified(store, _rule("无关键词命中但已验证"))
    candidate = _rule("导出参数包含关键词导出", verification=VerificationState.CANDIDATE)
    await store.put(candidate)

    retriever = SimpleRetriever(store)
    got = await retriever.retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": "1.0", "keywords": ["导出"]},
        limit=10,
    )
    ids = [r.id for r in got]
    assert verified.id in ids
    assert candidate.id not in ids, "CANDIDATE 默认必须排除——带/不带候选对照由此可靠"

    with_cands = SimpleRetriever(store, include_candidates=True)
    got2 = await with_cands.retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": "1.0", "keywords": ["导出"]},
        limit=10,
    )
    assert candidate.id in [r.id for r in got2]


def test_assembler_respects_budget() -> None:
    records = []
    for i in range(20):
        records.append(_rule(f"规则 {i}: " + "很长的说明文字 " * 8))
    assembler = TokenBudgetAssembler()
    text = assembler.assemble(records, token_budget=60)
    assert "[memory truncated]" in text
    assert len(text) // 4 <= 60 + 5  # 粗估 token 数不显著超预算
    assert text.split("\n")[0].startswith("- [rule|candidate] 规则 0")  # 原序保留


def test_assembler_empty_records() -> None:
    assert TokenBudgetAssembler().assemble([], token_budget=100) == ""


async def test_invalidated_rule_not_retrieved(store: SqliteMemoryStore) -> None:
    """I3 回归：失效(EXPIRED/SUPERSEDED)的已验证规则不得进入执行上下文。"""
    from agent_core.memory.record import LifecycleState, lifecycle_transition

    verified = await _verified(store, _rule("已失效的规则"))
    await store.put(lifecycle_transition(verified, LifecycleState.EXPIRED))

    retriever = SimpleRetriever(store)
    got = await retriever.retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": "1.0", "keywords": ["失效"]},
        limit=5,
    )
    assert got == [], "失效规则不得被召回"

    # 活跃规则不受影响
    fresh = await _verified(store, _rule("活跃规则"))
    got2 = await retriever.retrieve(
        scope={"namespaces": [NS_RULES]}, task_context={"environment_version": "1.0"}, limit=5
    )
    assert [r.id for r in got2] == [fresh.id]

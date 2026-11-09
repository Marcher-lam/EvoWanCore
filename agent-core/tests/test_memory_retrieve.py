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
    await store.put(_rule("新版本规则", environment_version="1.0"))
    await store.put(_rule("旧版本规则", environment_version="0.9"))

    retriever = SimpleRetriever(store)
    got = await retriever.retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": "1.0", "keywords": []},
        limit=10,
    )
    assert [r.content["statement"] for r in got] == ["新版本规则"]


async def test_candidate_never_outranks_verified(store: SqliteMemoryStore) -> None:
    verified = await _verified(store, _rule("无关键词命中但已验证"))
    candidate = _rule("导出参数包含关键词导出", verification=VerificationState.CANDIDATE)
    await store.put(candidate)

    retriever = SimpleRetriever(store)
    got = await retriever.retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": "1.0", "keywords": ["导出"]},
        limit=10,
    )
    assert got[0].id == verified.id
    assert got[1].id == candidate.id
    only_top = await retriever.retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": "1.0", "keywords": ["导出"]},
        limit=1,
    )
    assert [r.id for r in only_top] == [verified.id]


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

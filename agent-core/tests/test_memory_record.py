"""T12 MemoryRecord 双状态机 + 命名空间存储。"""

from pathlib import Path

import pytest
from agent_core.memory.record import (
    IllegalMemoryTransition,
    LifecycleState,
    MemoryKind,
    MemoryRecord,
    VerificationState,
    lifecycle_transition,
    verify_transition,
)
from agent_core.memory.store import NS_RULES, NS_SKILLS, SqliteMemoryStore


def _record(**over) -> MemoryRecord:
    base = dict(
        kind=MemoryKind.RULE,
        namespace=NS_RULES,
        owner="agent_1",
        content={"statement": "导出前必须确认选区", "conditions": {"app": "workbench"}},
        evidence_refs=["evi_1"],
        epistemic_status="verified",
        applicability={"app": "workbench", "env_version": "1.0"},
        environment_version="1.0",
    )
    base.update(over)
    return MemoryRecord(**base)


# ---------- 验证状态机 ----------


def test_verify_requires_evidence() -> None:
    rec = _record(evidence_refs=[], epistemic_status="observed")
    with pytest.raises(IllegalMemoryTransition, match="evidence"):
        verify_transition(rec, VerificationState.VERIFIED, evidence_ok=False)
    # 证据不足即使调用方传 ok 也不行：evidence_refs 为空直接拒绝
    with pytest.raises(IllegalMemoryTransition, match="evidence_refs"):
        verify_transition(rec, VerificationState.VERIFIED, evidence_ok=True)
    ok = verify_transition(_record(), VerificationState.VERIFIED, evidence_ok=True)
    assert ok.verification is VerificationState.VERIFIED


def test_new_records_start_as_candidate() -> None:
    rec = _record()
    assert rec.verification is VerificationState.CANDIDATE
    assert rec.lifecycle is LifecycleState.ACTIVE
    assert rec.revision == 1


def test_dispute_and_reject_paths() -> None:
    rec = _record()
    disputed = verify_transition(rec, VerificationState.DISPUTED)
    assert disputed.verification is VerificationState.DISPUTED
    reverified = verify_transition(disputed, VerificationState.VERIFIED, evidence_ok=True)
    assert reverified.verification is VerificationState.VERIFIED
    rejected = verify_transition(rec, VerificationState.REJECTED)
    with pytest.raises(IllegalMemoryTransition):
        verify_transition(rejected, VerificationState.VERIFIED, evidence_ok=True)  # 终态


def test_lifecycle_supersede_links() -> None:
    old = _record()
    superseded = lifecycle_transition(old, LifecycleState.SUPERSEDED, by="mem_new")
    assert superseded.lifecycle is LifecycleState.SUPERSEDED
    assert superseded.superseded_by == "mem_new"


def test_supersede_requires_by() -> None:
    with pytest.raises(IllegalMemoryTransition, match="superseded_by"):
        lifecycle_transition(_record(), LifecycleState.SUPERSEDED)


def test_tombstone_is_terminal() -> None:
    dead = lifecycle_transition(_record(), LifecycleState.TOMBSTONED)
    with pytest.raises(IllegalMemoryTransition):
        lifecycle_transition(dead, LifecycleState.ACTIVE)


# ---------- 存储 ----------


@pytest.fixture
def store(tmp_path: Path) -> SqliteMemoryStore:
    return SqliteMemoryStore(tmp_path / "mem.db")


async def test_put_get_roundtrip(store: SqliteMemoryStore) -> None:
    rec = _record()
    await store.put(rec)
    loaded = await store.get(rec.id)
    assert loaded == rec
    assert await store.get("missing") is None


async def test_query_by_namespace_and_applicability(store: SqliteMemoryStore) -> None:
    r1 = _record(applicability={"app": "workbench"})
    r2 = _record(
        namespace=NS_SKILLS,
        kind=MemoryKind.SKILL_REF,
        applicability={"app": "other"},
    )
    await store.put(r1)
    await store.put(r2)

    rules = await store.query(NS_RULES, {})
    assert [r.id for r in rules] == [r1.id]

    matched = await store.query(NS_RULES, {"applicability": {"app": "workbench"}})
    assert [r.id for r in matched] == [r1.id]

    nomatch = await store.query(NS_RULES, {"applicability": {"app": "nope"}})
    assert nomatch == []


async def test_tombstone(store: SqliteMemoryStore) -> None:
    rec = _record()
    await store.put(rec)
    dead = lifecycle_transition(rec, LifecycleState.TOMBSTONED)
    await store.put(dead)
    loaded = await store.get(rec.id)
    assert loaded.lifecycle is LifecycleState.TOMBSTONED
    # 逻辑失效：默认查询不返回 TOMBSTONED
    active = await store.query(NS_RULES, {})
    assert active == []


async def test_revision_increments_on_put(store: SqliteMemoryStore) -> None:
    rec = _record()
    await store.put(rec)
    updated = rec.model_copy(update={"content": {"statement": "修订版"}})
    await store.put(updated)
    loaded = await store.get(rec.id)
    assert loaded.revision == 2
    assert loaded.content["statement"] == "修订版"

"""T14 MemoryWriter/Consolidator/Publisher：按主张验证、冲突不覆盖、失效传播。"""

from pathlib import Path

import pytest
from agent_core.memory.record import (
    MemoryKind,
    MemoryRecord,
    VerificationState,
)
from agent_core.memory.store import NS_RULES, SqliteMemoryStore
from agent_core.memory.writer import (
    ConflictResolution,
    DefaultConsolidator,
    DefaultMemoryWriter,
    VersionedPublisher,
)


@pytest.fixture
async def store(tmp_path: Path) -> SqliteMemoryStore:
    return SqliteMemoryStore(tmp_path / "mem.db")


# ---------- Writer：候选从不直接可信 ----------


async def test_propose_never_verified(store: SqliteMemoryStore) -> None:
    writer = DefaultMemoryWriter(store)
    rec = await writer.propose(
        kind=MemoryKind.RULE,
        content={
            "statement": "导出需先确认选区",
            "claims": ["export_scope"],
            "evidence_checked": ["export_scope"],
        },
        applicability={"app": "workbench"},
        evidence_refs=["evi_1"],
        source_refs=["practice_1"],
    )
    assert rec.verification is VerificationState.CANDIDATE
    assert rec.epistemic_status in ("observed", "hypothesized")


async def test_validate_rejects_unsupported_claims(store: SqliteMemoryStore) -> None:
    writer = DefaultMemoryWriter(store)
    overreach = await writer.propose(
        kind=MemoryKind.RULE,
        content={
            "statement": "该参数组合最优",
            "claims": ["optimal_params"],
            "evidence_checked": ["file_exists"],
        },
        applicability={},
        evidence_refs=["evi_open"],
    )
    report = await writer.validate(overreach)
    assert report.valid is False
    assert any("optimal_params" in r for r in report.reasons)

    modest = await writer.propose(
        kind=MemoryKind.RULE,
        content={
            "statement": "文件可打开",
            "claims": ["file_exists"],
            "evidence_checked": ["file_exists"],
        },
        applicability={},
        evidence_refs=["evi_open"],
    )
    assert (await writer.validate(modest)).valid is True


async def test_commit_stores_candidate(store: SqliteMemoryStore) -> None:
    writer = DefaultMemoryWriter(store)
    rec = await writer.propose(
        kind=MemoryKind.RULE,
        content={"statement": "x", "claims": [], "evidence_checked": []},
        applicability={},
        evidence_refs=[],
    )
    committed = await writer.commit(rec)
    assert (await store.get(committed.id)).content["statement"] == "x"


# ---------- Consolidator：冲突不覆盖 ----------


def _conflicting(store_id: str, conclusion: bool) -> MemoryRecord:
    return MemoryRecord(
        kind=MemoryKind.RULE,
        namespace=NS_RULES,
        content={"subject": "xlsx导出保留音轨", "conclusion": conclusion},
        evidence_refs=["evi"],
        applicability={"app": "workbench", "format": "xlsx"},
    )


async def test_conflict_same_conditions_marks_both_disputed(store: SqliteMemoryStore) -> None:
    await store.put(_conflicting("a", True))
    await store.put(_conflicting("b", False))
    records = await store.query(NS_RULES, {})

    consolidator = DefaultConsolidator(store)
    conflicts = await consolidator.detect_conflicts(records)
    assert len(conflicts) == 1
    assert conflicts[0].dimension == "conclusion"

    resolution: ConflictResolution = await consolidator.resolve(conflicts[0])
    assert resolution.action == "dispute"
    assert resolution.contrast_task_hint["family"] == "contrast"
    assert "音轨" in str(resolution.contrast_task_hint["subject"])

    for rid in (resolution.record_a_id, resolution.record_b_id):
        loaded = await store.get(rid)
        assert loaded.verification is VerificationState.DISPUTED
        assert loaded.lifecycle.value == "active"  # 没有被覆盖或删除


async def test_no_conflict_when_conditions_differ(store: SqliteMemoryStore) -> None:
    r1 = _conflicting("a", True)
    r2 = MemoryRecord(
        kind=MemoryKind.RULE,
        namespace=NS_RULES,
        content={"subject": "xlsx导出保留音轨", "conclusion": False},
        evidence_refs=["evi"],
        applicability={"app": "workbench", "format": "csv"},  # 条件不同
    )
    await store.put(r1)
    await store.put(r2)
    consolidator = DefaultConsolidator(store)
    assert await consolidator.detect_conflicts(await store.query(NS_RULES, {})) == []


# ---------- Publisher：晋级与失效传播 ----------


async def test_publish_requires_verifier_report(store: SqliteMemoryStore) -> None:
    writer = DefaultMemoryWriter(store)
    publisher = VersionedPublisher(store)
    rec = await writer.propose(
        kind=MemoryKind.RULE,
        content={
            "statement": "导出后重开校验通过",
            "claims": ["reopen_ok"],
            "evidence_checked": ["reopen_ok"],
        },
        applicability={},
        evidence_refs=["evi_1"],
    )
    committed = await writer.commit(rec)
    published = await publisher.publish(
        committed.id, verifier_report={"checks": [{"reopen_ok": True}]}
    )
    assert published.verification is VerificationState.VERIFIED
    assert published.content["verification_report"]["checks"][0]["reopen_ok"] is True


async def test_invalidate_propagates_to_dependents(store: SqliteMemoryStore) -> None:
    writer = DefaultMemoryWriter(store)
    publisher = VersionedPublisher(store)

    premise = await writer.commit(
        await writer.propose(
            kind=MemoryKind.RULE,
            content={"statement": "前提规则", "claims": ["p"], "evidence_checked": ["p"]},
            applicability={},
            evidence_refs=["evi_p"],
        )
    )
    dependent = await writer.commit(
        await writer.propose(
            kind=MemoryKind.RULE,
            content={"statement": "依赖前提的规则", "claims": ["d"], "evidence_checked": ["d"]},
            applicability={},
            evidence_refs=["evi_d"],
        )
    )
    dependent_with_link = dependent.model_copy(update={"dependency_refs": [premise.id]})
    await store.put(dependent_with_link)

    await publisher.invalidate(premise.id, reason="发现反例")

    dead = await store.get(premise.id)
    assert dead.lifecycle.value == "expired"
    marked = await store.get(dependent.id)
    assert marked.verification is VerificationState.DISPUTED  # 派生结论自动待复核


async def test_publish_refuses_failed_verifier_report(store: SqliteMemoryStore) -> None:
    """I1 防线：验收报告未通过，禁止晋级 VERIFIED。"""
    writer = DefaultMemoryWriter(store)
    publisher = VersionedPublisher(store)
    committed = await writer.commit(
        await writer.propose(
            kind=MemoryKind.RULE,
            content={"statement": "x", "claims": ["c"], "evidence_checked": ["c"]},
            applicability={},
            evidence_refs=["evi"],
        )
    )
    with pytest.raises(Exception, match="验收报告未通过"):
        await publisher.publish(committed.id, verifier_report={"passed": False, "checks": []})
    still = await store.get(committed.id)
    assert still.verification is VerificationState.CANDIDATE  # 未被晋级

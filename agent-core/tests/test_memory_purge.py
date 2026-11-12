"""T10 记忆物理清除：delete 全版本 DELETE + 审计事件。"""

from pathlib import Path

import pytest
from agent_core.memory.record import MemoryRecord
from agent_core.memory.store import NS_RULES, SqliteMemoryStore


def _record(rid: str = "mem_purge_1") -> MemoryRecord:
    return MemoryRecord(
        id=rid, namespace=NS_RULES, kind="rule",
        content={"rule": "convert before extract"},
    )


async def test_purge_deletes_and_audits(tmp_path: Path) -> None:
    """物理清除：全版本 DELETE + 审计事件落盘。"""
    from agent_core.memory.purge import SqlitePurgeAuditor, purge_memory

    store = SqliteMemoryStore(tmp_path / "mem.db")
    auditor = SqlitePurgeAuditor(tmp_path / "purge_audit.db")

    # 2 个 revision（put 即新版本）
    await store.put(_record())
    await store.put(_record())
    assert await store.get("mem_purge_1") is not None

    audit = await purge_memory(
        store, auditor, NS_RULES, "mem_purge_1", reason="gdpr-erasure",
    )
    assert audit.record_id == "mem_purge_1"
    assert audit.deleted_revisions == 2
    assert audit.reason == "gdpr-erasure"

    # 物理清除：get 为 None，且 DB 中该 id 的所有 revision 行数为 0
    assert await store.get("mem_purge_1") is None
    import aiosqlite

    async with aiosqlite.connect(tmp_path / "mem.db") as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id = ?", ("mem_purge_1",)
        )
        row = await cur.fetchone()
    assert row[0] == 0, "所有 revision 物理删除（非 tombstone）"

    # 审计可查询
    events = await auditor.list_events()
    assert len(events) == 1
    assert events[0].record_id == "mem_purge_1"
    assert events[0].namespace == NS_RULES
    assert events[0].deleted_revisions == 2


async def test_delete_wrong_namespace_refused(tmp_path: Path) -> None:
    """跨命名空间 delete 拒绝（防误删他 ns 记录）。"""
    store = SqliteMemoryStore(tmp_path / "mem.db")
    await store.put(_record())
    with pytest.raises(KeyError):
        await store.delete("skills", "mem_purge_1")
    assert await store.get("mem_purge_1") is not None, "原记录未动"


async def test_delete_nonexistent_raises(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    with pytest.raises(KeyError):
        await store.delete(NS_RULES, "mem_ghost")


async def test_tombstone_still_available_then_purge(tmp_path: Path) -> None:
    """先逻辑失效再物理清除：tombstone 后仍可 purge（含 TOMBSTONED revision）。"""
    from agent_core.memory.purge import SqlitePurgeAuditor, purge_memory

    store = SqliteMemoryStore(tmp_path / "mem.db")
    await store.put(_record())
    await store.tombstone("mem_purge_1")
    auditor = SqlitePurgeAuditor(tmp_path / "purge_audit.db")
    audit = await purge_memory(
        store, auditor, NS_RULES, "mem_purge_1", reason="cleanup"
    )
    assert audit.deleted_revisions == 2, "ACTIVE + TOMBSTONED 两版本全删"

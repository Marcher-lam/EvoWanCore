"""MemoryStore — 命名空间化记忆存储（spec §5.4）。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import aiosqlite

from agent_core.memory.record import LifecycleState, MemoryRecord

NS_SKILLS = "skills"
NS_RULES = "rules"
NS_GAPS = "gaps"
NS_COVERAGE = "coverage"
NS_FAILURES = "failures"
NS_PROFILES = "profiles"
NS_RELEASES = "releases"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    namespace TEXT NOT NULL,
    data TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (id, revision)
);
CREATE INDEX IF NOT EXISTS idx_memories_ns ON memories(namespace);
"""


class MemoryStore(Protocol):
    async def put(self, record: MemoryRecord) -> None: ...
    async def get(self, record_id: str) -> MemoryRecord | None: ...
    async def query(self, namespace: str, filter: dict) -> list[MemoryRecord]: ...
    async def tombstone(self, record_id: str) -> None: ...
    async def delete(self, namespace: str, record_id: str) -> int: ...


class SqliteMemoryStore:
    """版本化存储：同一 id 的 put 产生新 revision；get 返回最新版本。

    query 默认只返回 lifecycle=ACTIVE 的当前版本；TOMBSTONED 是逻辑失效，
    历史版本保留；物理清除走 delete()（V5：全 revision DELETE + 审计，
    见 memory/purge.py）。
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._schema_ready = False

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        if not self._schema_ready:
            await conn.executescript(_SCHEMA)
            await conn.commit()
            self._schema_ready = True
        return conn

    async def put(self, record: MemoryRecord) -> None:
        """put 即新版本：revision 由存储层自增，调用方无需管理。"""
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT MAX(revision) FROM memories WHERE id = ?", (record.id,)
            )
            row = await cur.fetchone()
            next_rev = (row[0] or 0) + 1
            versioned = record.model_copy(update={"revision": next_rev})
            await conn.execute(
                "UPDATE memories SET is_current = 0 WHERE id = ? AND is_current = 1",
                (record.id,),
            )
            await conn.execute(
                "INSERT INTO memories (id, revision, namespace, data, is_current)"
                " VALUES (?,?,?,?,1)",
                (versioned.id, next_rev, versioned.namespace, versioned.model_dump_json()),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get(self, record_id: str) -> MemoryRecord | None:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT data FROM memories WHERE id = ? AND is_current = 1", (record_id,)
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        return MemoryRecord.model_validate_json(row[0]) if row else None

    async def query(self, namespace: str, filter: dict) -> list[MemoryRecord]:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT data FROM memories WHERE namespace = ? AND is_current = 1",
                (namespace,),
            )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        records = [MemoryRecord.model_validate_json(r[0]) for r in rows]
        # 逻辑失效：默认查询不返回
        records = [r for r in records if r.lifecycle is not LifecycleState.TOMBSTONED]
        if "kind" in filter:
            records = [r for r in records if r.kind.value == filter["kind"]]
        if "environment_version" in filter:
            records = [r for r in records if r.environment_version == filter["environment_version"]]
        if "applicability" in filter:
            wanted = filter["applicability"]
            records = [
                r for r in records if all(r.applicability.get(k) == v for k, v in wanted.items())
            ]
        if "verification" in filter:
            records = [r for r in records if r.verification.value == filter["verification"]]
        return records

    async def tombstone(self, record_id: str) -> None:
        current = await self.get(record_id)
        if current is None:
            raise KeyError(f"memory {record_id} 不存在")
        dead = current.model_copy(update={"lifecycle": LifecycleState.TOMBSTONED})
        await self.put(dead)

    async def delete(self, namespace: str, record_id: str) -> int:
        """物理清除：删除该 id 的所有 revision（V5：含 TOMBSTONED 历史）。

        命名空间守卫：记录实际 ns 与入参不符 → KeyError 拒绝（防跨 ns 误删）。
        返回被删除的 revision 行数；不存在 → KeyError。
        """
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT COUNT(*), MAX(namespace) FROM memories WHERE id = ?",
                (record_id,),
            )
            row = await cur.fetchone()
            count, actual_ns = row[0], row[1]
            if count == 0:
                raise KeyError(f"memory {record_id} 不存在")
            if actual_ns != namespace:
                raise KeyError(
                    f"memory {record_id} 属于 {actual_ns}，拒绝从 {namespace} 删除"
                )
            cur = await conn.execute(
                "DELETE FROM memories WHERE id = ?", (record_id,)
            )
            await conn.commit()
            return cur.rowcount
        finally:
            await conn.close()

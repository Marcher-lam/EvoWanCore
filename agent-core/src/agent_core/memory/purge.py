"""记忆物理清除 + 审计（V5 T10）。

purge_memory(store, auditor, namespace, record_id, reason)：
物理 DELETE 全部 revision → 审计事件落盘（谁/何时/为何/删了几版）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from pydantic import BaseModel, Field

from agent_core.memory.store import SqliteMemoryStore

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS purge_audit (
    event_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    deleted_revisions INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""


class PurgeAuditEvent(BaseModel):
    """一次物理清除的审计记录。"""

    event_id: str = Field(
        default_factory=lambda: f"purge_{uuid.uuid4().hex[:12]}"
    )
    record_id: str
    namespace: str
    deleted_revisions: int
    reason: str
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SqlitePurgeAuditor:
    """审计事件落盘（独立 sqlite，与业务库分离）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._schema_ready = False

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        if not self._schema_ready:
            await conn.executescript(_AUDIT_SCHEMA)
            await conn.commit()
            self._schema_ready = True
        return conn

    async def append(self, event: PurgeAuditEvent) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO purge_audit"
                " (event_id, record_id, namespace, deleted_revisions, reason, ts)"
                " VALUES (?,?,?,?,?,?)",
                (
                    event.event_id, event.record_id, event.namespace,
                    event.deleted_revisions, event.reason, event.ts,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def list_events(self, record_id: str | None = None) -> list[PurgeAuditEvent]:
        conn = await self._connect()
        try:
            if record_id is None:
                cur = await conn.execute(
                    "SELECT event_id, record_id, namespace, deleted_revisions,"
                    " reason, ts FROM purge_audit ORDER BY ts"
                )
            else:
                cur = await conn.execute(
                    "SELECT event_id, record_id, namespace, deleted_revisions,"
                    " reason, ts FROM purge_audit WHERE record_id = ? ORDER BY ts",
                    (record_id,),
                )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        return [
            PurgeAuditEvent(
                event_id=r[0], record_id=r[1], namespace=r[2],
                deleted_revisions=r[3], reason=r[4], ts=r[5],
            )
            for r in rows
        ]


async def purge_memory(
    store: SqliteMemoryStore,
    auditor: SqlitePurgeAuditor,
    namespace: str,
    record_id: str,
    *,
    reason: str,
) -> PurgeAuditEvent:
    """物理清除一条记忆（全 revision）并记录审计事件。"""
    deleted = await store.delete(namespace, record_id)
    event = PurgeAuditEvent(
        record_id=record_id,
        namespace=namespace,
        deleted_revisions=deleted,
        reason=reason,
    )
    await auditor.append(event)
    return event

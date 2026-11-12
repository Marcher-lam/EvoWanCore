"""append-only 事件日志（spec §5.3）。事件是已发生的运行事实，只增不改。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Protocol

import aiosqlite

from agent_core.events import RuntimeEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    ts TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    causation_id TEXT,
    correlation_id TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id);
"""


class EventLog(Protocol):
    async def append(self, event: RuntimeEvent) -> None: ...

    def iter_events(self, correlation_id: str | None = None) -> AsyncIterator[RuntimeEvent]: ...


class SqliteEventLog:
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

    async def append(self, event: RuntimeEvent) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO events (id, type, ts, subject_type, subject_id, causation_id,"
                " correlation_id, payload) VALUES (?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.type,
                    event.ts.isoformat(),
                    event.subject_type,
                    event.subject_id,
                    event.causation_id,
                    event.correlation_id,
                    json.dumps(event.payload),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def iter_events(self, correlation_id: str | None = None) -> AsyncIterator[RuntimeEvent]:
        conn = await self._connect()
        try:
            if correlation_id is None:
                cur = await conn.execute(
                    "SELECT id, type, ts, subject_type, subject_id, causation_id,"
                    " correlation_id, payload FROM events ORDER BY seq"
                )
            else:
                cur = await conn.execute(
                    "SELECT id, type, ts, subject_type, subject_id, causation_id,"
                    " correlation_id, payload FROM events WHERE correlation_id = ? ORDER BY seq",
                    (correlation_id,),
                )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        for row in rows:
            yield RuntimeEvent(
                id=row[0],
                type=row[1],
                ts=datetime.fromisoformat(row[2]),
                subject_type=row[3],
                subject_id=row[4],
                causation_id=row[5],
                correlation_id=row[6],
                payload=json.loads(row[7]),
            )

    async def iter_events_with_seq(
        self, correlation_id: str, after_seq: int = -1
    ) -> AsyncIterator[tuple[int, RuntimeEvent]]:
        """seq 游标增量读（V7-T2）：只返回 seq > after_seq 的事件，O(增量) 而非 O(全量)。

        SSE 长连接轮询专用——每轮只读新事件，消除全量 fetchall 的 O(n²) 累计。
        """
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT seq, id, type, ts, subject_type, subject_id, causation_id,"
                " correlation_id, payload FROM events"
                " WHERE correlation_id = ? AND seq > ? ORDER BY seq",
                (correlation_id, after_seq),
            )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        for row in rows:
            yield row[0], RuntimeEvent(
                id=row[1],
                type=row[2],
                ts=datetime.fromisoformat(row[3]),
                subject_type=row[4],
                subject_id=row[5],
                causation_id=row[6],
                correlation_id=row[7],
                payload=json.loads(row[8]),
            )

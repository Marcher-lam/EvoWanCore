"""StateStore — Run/Task 状态与执行检查点（spec §5.3）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import aiosqlite

from agent_core.run import RunHandle
from agent_core.task import TaskHandle

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id);
"""


class StateStore(Protocol):
    async def save_run(self, run: RunHandle) -> None: ...
    async def load_run(self, run_id: str) -> RunHandle | None: ...
    async def save_task(self, task: TaskHandle) -> None: ...
    async def load_task(self, task_id: str) -> TaskHandle | None: ...
    async def save_checkpoint(self, run_id: str, state: dict) -> None: ...
    async def load_latest_checkpoint(self, run_id: str) -> dict | None: ...


class SqliteStateStore:
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

    async def save_run(self, run: RunHandle) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO runs (id, data) VALUES (?,?)",
                (run.id, run.model_dump_json()),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def load_run(self, run_id: str) -> RunHandle | None:
        conn = await self._connect()
        try:
            cur = await conn.execute("SELECT data FROM runs WHERE id = ?", (run_id,))
            row = await cur.fetchone()
        finally:
            await conn.close()
        return RunHandle.model_validate_json(row[0]) if row else None

    async def save_task(self, task: TaskHandle) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO tasks (id, data) VALUES (?,?)",
                (task.id, task.model_dump_json()),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def load_task(self, task_id: str) -> TaskHandle | None:
        conn = await self._connect()
        try:
            cur = await conn.execute("SELECT data FROM tasks WHERE id = ?", (task_id,))
            row = await cur.fetchone()
        finally:
            await conn.close()
        return TaskHandle.model_validate_json(row[0]) if row else None

    async def save_checkpoint(self, run_id: str, state: dict) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO checkpoints (run_id, created_at, data) VALUES (?,?,?)",
                (run_id, datetime.now(UTC).isoformat(), json.dumps(state)),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def load_latest_checkpoint(self, run_id: str) -> dict | None:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT data FROM checkpoints WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                (run_id,),
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        return json.loads(row[0]) if row else None

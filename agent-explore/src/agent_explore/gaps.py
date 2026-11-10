"""GapLedger — 能力缺口的正式对象（spec §5.3 GapLedger 契约）。

"不知道怎么做"和"没有权力做"必须分开：授权缺口固定 WAITING_EXTERNAL，
不自动绕过；解锁只能来自外部显式声明。
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import aiosqlite
from pydantic import BaseModel, Field


class GapType(StrEnum):
    KNOWLEDGE = "knowledge"
    SKILL = "skill"
    TOOL = "tool"
    PERCEPTION = "perception"
    AUTHORIZATION = "authorization"
    VERIFICATION = "verification"
    ENVIRONMENT = "environment"


class GapState(StrEnum):
    DISCOVERED = "discovered"
    TRIAGED = "triaged"
    ACTIONABLE = "actionable"
    WAITING_EXTERNAL = "waiting_external"
    PATCH_CANDIDATE_READY = "patch_candidate_ready"
    UNDER_EVALUATION = "under_evaluation"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


_CLOSED = {GapState.RESOLVED, GapState.WONT_FIX}


class Gap(BaseModel):
    id: str = Field(default_factory=lambda: f"gap_{uuid.uuid4().hex[:12]}")
    gap_type: GapType
    title: str
    description: str = ""
    severity: float = 0.5
    recurrence: int = 1
    status: GapState = GapState.DISCOVERED
    reproduce_conditions: dict = {}
    evidence_refs: list[str] = []
    closure_condition: str = ""
    affected_capabilities: list[str] = []
    external_unlock: bool = False  # 授权缺口解锁标记：只能由外部显式给


class GapLedger(Protocol):
    async def record(self, gap: Gap) -> str: ...
    async def update(self, gap: Gap) -> None: ...
    async def open_gaps(self, filter: dict) -> list[Gap]: ...
    async def close(self, gap_id: str, resolution: str) -> None: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS gaps (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


def _enforce_auth_gate(gap: Gap) -> Gap:
    """授权缺口：非外部解锁时固定 WAITING_EXTERNAL。"""
    if gap.gap_type is GapType.AUTHORIZATION and not gap.external_unlock:
        if gap.status not in (GapState.WAITING_EXTERNAL, *_CLOSED):
            return gap.model_copy(update={"status": GapState.WAITING_EXTERNAL})
    return gap


class SqliteGapLedger:
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

    async def record(self, gap: Gap) -> str:
        gap = _enforce_auth_gate(gap)
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO gaps (id, data) VALUES (?,?)",
                (gap.id, gap.model_dump_json()),
            )
            await conn.commit()
        finally:
            await conn.close()
        return gap.id

    async def update(self, gap: Gap) -> None:
        await self.record(gap)

    async def open_gaps(self, filter: dict) -> list[Gap]:
        conn = await self._connect()
        try:
            cur = await conn.execute("SELECT data FROM gaps")
            rows = await cur.fetchall()
        finally:
            await conn.close()
        gaps = [Gap.model_validate_json(r[0]) for r in rows]
        gaps = [g for g in gaps if g.status not in _CLOSED]
        if "gap_type" in filter:
            gaps = [g for g in gaps if g.gap_type.value == filter["gap_type"]]
        if "min_severity" in filter:
            gaps = [g for g in gaps if g.severity >= filter["min_severity"]]
        return sorted(gaps, key=lambda g: -g.severity)

    async def close(self, gap_id: str, resolution: str) -> None:
        conn = await self._connect()
        try:
            cur = await conn.execute("SELECT data FROM gaps WHERE id = ?", (gap_id,))
            row = await cur.fetchone()
            if row is None:
                raise KeyError(f"gap {gap_id} 不存在")
            gap = Gap.model_validate_json(row[0])
            resolved = gap.model_copy(
                update={"status": GapState.RESOLVED, "description": gap.description,
                        "closure_condition": resolution}
            )
            await conn.execute(
                "UPDATE gaps SET data = ? WHERE id = ?",
                (resolved.model_dump_json(), gap_id),
            )
            await conn.commit()
        finally:
            await conn.close()

"""Asset / Segment / Observation / Evidence 与 EvidenceStore（spec §5.3、§5.5）。

可回到原始证据：OCR/ASR/摘要都是带血缘的派生结果；原始媒体按 sha256
内容寻址存储，同内容只存一份，永不覆盖。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import aiosqlite
from pydantic import BaseModel, Field

AssetKind = Literal["image", "audio", "video", "document", "structured", "binary"]
VerificationStatus = Literal["unverified", "verified", "disputed"]


class Asset(BaseModel):
    id: str
    kind: AssetKind
    storage_ref: str
    sha256: str
    permissions: dict = {}
    source: str = ""
    version: int = 1


class Segment(BaseModel):
    """时空切片：视频帧区间、音频区间、页面/对象区域。"""

    id: str = Field(default_factory=lambda: f"seg_{uuid.uuid4().hex[:12]}")
    asset_id: str
    time_range: tuple[float, float] | None = None
    spatial: dict | None = None  # 例如 {"page": 2, "bbox": [x0, y0, x1, y1]}
    channel: str | None = None


class Observation(BaseModel):
    id: str = Field(default_factory=lambda: f"obs_{uuid.uuid4().hex[:12]}")
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    environment_version: str = "unspecified"
    asset_refs: list[str] = []
    state_summary: dict = {}


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: f"evi_{uuid.uuid4().hex[:12]}")
    claim: dict
    observation_refs: list[str]
    derivation: dict = {}
    verification_status: VerificationStatus = "unverified"


class EvidenceStore(Protocol):
    async def write_asset(
        self, data: bytes, *, kind: AssetKind, source: str = "", permissions: dict | None = None
    ) -> Asset: ...

    async def write_observation(self, observation: Observation) -> str: ...

    async def write_evidence(
        self, claim: dict, observation_refs: list[str], derivation: dict | None = None
    ) -> str: ...

    async def trace_lineage(self, evidence_id: str) -> dict: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    storage_ref TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    permissions TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    environment_version TEXT NOT NULL,
    asset_refs TEXT NOT NULL,
    state_summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidences (
    id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    observation_refs TEXT NOT NULL,
    derivation TEXT NOT NULL DEFAULT '{}',
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    created_at TEXT NOT NULL
);
"""


class SqliteEvidenceStore:
    """SQLite + 内容寻址 blob 目录的默认实现。"""

    def __init__(self, db_path: Path | str, blob_dir: Path | str) -> None:
        self.db_path = Path(db_path)
        self.blob_dir = Path(blob_dir)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._schema_ready = False

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        if not self._schema_ready:
            await conn.executescript(_SCHEMA)
            await conn.commit()
            self._schema_ready = True
        return conn

    async def write_asset(
        self,
        data: bytes,
        *,
        kind: AssetKind,
        source: str = "",
        permissions: dict | None = None,
    ) -> Asset:
        sha = hashlib.sha256(data).hexdigest()
        blob_path = self.blob_dir / sha
        if not blob_path.exists():
            blob_path.write_bytes(data)  # 已存在则不动：内容寻址，永不覆盖
        asset = Asset(
            id=f"asset_{sha[:16]}",
            kind=kind,
            storage_ref=str(blob_path),
            sha256=sha,
            permissions=permissions or {},
            source=source,
        )
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO assets (id, kind, storage_ref, sha256, permissions,"
                " source, version, created_at) VALUES (?,?,?,?,?,?,1,?)",
                (
                    asset.id,
                    asset.kind,
                    asset.storage_ref,
                    asset.sha256,
                    json.dumps(asset.permissions),
                    asset.source,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        return asset

    async def write_observation(self, observation: Observation) -> str:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO observations (id, ts, environment_version, asset_refs,"
                " state_summary) VALUES (?,?,?,?,?)",
                (
                    observation.id,
                    observation.ts.isoformat(),
                    observation.environment_version,
                    json.dumps(observation.asset_refs),
                    json.dumps(observation.state_summary),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        return observation.id

    async def write_evidence(
        self, claim: dict, observation_refs: list[str], derivation: dict | None = None
    ) -> str:
        evidence = Evidence(
            claim=claim, observation_refs=observation_refs, derivation=derivation or {}
        )
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO evidences (id, claim, observation_refs, derivation,"
                " verification_status, created_at) VALUES (?,?,?,?,?,?)",
                (
                    evidence.id,
                    json.dumps(evidence.claim),
                    json.dumps(evidence.observation_refs),
                    json.dumps(evidence.derivation),
                    evidence.verification_status,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        return evidence.id

    async def trace_lineage(self, evidence_id: str) -> dict:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT id, claim, observation_refs, derivation, verification_status FROM"
                " evidences WHERE id = ?",
                (evidence_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise KeyError(f"evidence {evidence_id} 不存在")
            obs_ids = json.loads(row[2])
            placeholders = ",".join("?" for _ in obs_ids)
            cur = await conn.execute(
                "SELECT id, ts, environment_version, asset_refs, state_summary"
                f" FROM observations WHERE id IN ({placeholders})",  # noqa: S608
                obs_ids,
            )
            obs_rows = await cur.fetchall()
        finally:
            await conn.close()
        return {
            "evidence": {
                "id": row[0],
                "claim": json.loads(row[1]),
                "derivation": json.loads(row[3]),
                "verification_status": row[4],
            },
            "observations": [
                {
                    "id": r[0],
                    "ts": r[1],
                    "environment_version": r[2],
                    "asset_refs": json.loads(r[3]),
                    "state_summary": json.loads(r[4]),
                }
                for r in obs_rows
            ],
        }

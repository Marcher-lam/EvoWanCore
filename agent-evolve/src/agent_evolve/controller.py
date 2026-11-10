"""EvolutionController — 候选→评测→发布→回滚（spec §6，GEPA 式门禁）。

发布门槛：improved 且无回归。回滚生成反向 manifest，记忆候选走失效链。
候选可以修改产品，但不能修改自己的验收标准并据此证明通过。
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import uuid
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agent_core.events import new_event
from agent_core.memory.record import MemoryKind, MemoryRecord
from agent_core.memory.store import NS_RELEASES, MemoryStore
from agent_core.memory.writer import MemoryPublisher
from agent_core.task import TaskSpec
from pydantic import BaseModel, Field

ScoreRunner = Callable[[TaskSpec], Awaitable[float]]
DualRunner = Callable[[TaskSpec, bool], Awaitable[float]]  # (task, with_candidate)


CandidateType = Literal["memory", "skill", "prompt_policy"]


class ExperienceCandidate(BaseModel):
    id: str = Field(default_factory=lambda: f"cand_{uuid.uuid4().hex[:12]}")
    candidate_type: CandidateType
    payload: dict
    source_practice_ids: list[str] = []
    parent_release_id: str | None = None


class EvalReport(BaseModel):
    mini_batch_scores: list[float]
    parent_scores: list[float] | None = None
    improved: bool = False
    regression_violations: list[str] = []


class ReleaseManifest(BaseModel):
    release_id: str = Field(default_factory=lambda: f"rel_{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    models: dict = {}
    capabilities: dict = {}
    memory_snapshot_id: str = ""
    candidates: list[str] = []
    rollback_of: str | None = None
    note: str = ""


class PublishDenied(Exception):
    """候选未过发布门槛。"""


_STATE_DDL = (
    "CREATE TABLE IF NOT EXISTS evolution_state ("
    " kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,"
    " PRIMARY KEY (kind, id))"
)


class EvolutionController:
    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        memory_publisher: MemoryPublisher,
        db_path: Path | None = None,
        event_log=None,
    ) -> None:
        self._memory_store = memory_store
        self._memory_publisher = memory_publisher
        self._candidates: dict[str, ExperienceCandidate] = {}
        self._manifests: dict[str, ReleaseManifest] = {}
        self._db_path = Path(db_path) if db_path is not None else None
        self._event_log = event_log
        if self._db_path is not None:
            self._load_state()

    # ---------- M10：演化态持久化（candidates/manifests 落 SQLite） ----------

    def _load_state(self) -> None:
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.execute(_STATE_DDL)
            rows = conn.execute("SELECT kind, id, payload FROM evolution_state").fetchall()
        for kind, obj_id, payload in rows:
            if kind == "candidate":
                self._candidates[obj_id] = ExperienceCandidate(**json.loads(payload))
            elif kind == "manifest":
                self._manifests[obj_id] = ReleaseManifest(**json.loads(payload))

    def _persist(self, kind: str, obj_id: str, payload: dict) -> None:
        if self._db_path is None:
            return
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.execute(_STATE_DDL)
            conn.execute(
                "INSERT OR REPLACE INTO evolution_state (kind, id, payload) VALUES (?, ?, ?)",
                (kind, obj_id, json.dumps(payload, ensure_ascii=False)),
            )

    async def _deny(self, candidate_id: str, reason: str) -> None:
        """拒绝发布：写 PublishDenied 事件（可选注入 EventLog）后抛错。"""
        if self._event_log is not None:
            await self._event_log.append(
                new_event("PublishDenied", "release", candidate_id, reason=reason)
            )
        raise PublishDenied(reason)

    def submit(self, candidate: ExperienceCandidate) -> str:
        self._candidates[candidate.id] = candidate
        self._persist("candidate", candidate.id, candidate.model_dump())
        return candidate.id

    async def evaluate(
        self,
        candidate_id: str,
        mini_batch: list[TaskSpec],
        runner: DualRunner,
        regression_scenarios: list[TaskSpec] | None = None,
    ) -> EvalReport:
        candidate_scores = [await runner(t, with_candidate=True) for t in mini_batch]
        parent_scores = [await runner(t, with_candidate=False) for t in mini_batch]

        violations: list[str] = []
        for t in regression_scenarios or []:
            base = await runner(t, with_candidate=False)
            with_cand = await runner(t, with_candidate=True)
            if with_cand < base:
                violations.append(t.id)

        cand_mean = statistics.mean(candidate_scores) if candidate_scores else 0.0
        parent_mean = statistics.mean(parent_scores) if parent_scores else 0.0
        improved = cand_mean > parent_mean or (not parent_scores and cand_mean > 0)
        return EvalReport(
            mini_batch_scores=candidate_scores,
            parent_scores=parent_scores,
            improved=improved,
            regression_violations=violations,
        )

    async def publish(
        self,
        candidate_id: str,
        report: EvalReport,
        *,
        memory_snapshot_id: str = "",
        models: dict | None = None,
        capabilities: dict | None = None,
    ) -> ReleaseManifest:
        if candidate_id not in self._candidates:
            raise KeyError(f"candidate {candidate_id} 不存在")
        if not report.improved:
            await self._deny(candidate_id, "未达发布门槛: improved=False（候选未优于父版本）")
        if report.regression_violations:
            await self._deny(
                candidate_id,
                f"未达发布门槛: regression 回归 {report.regression_violations}"
                "（新知识不得破坏旧能力）",
            )
        manifest = ReleaseManifest(
            models=models or {},
            capabilities=capabilities or {},
            memory_snapshot_id=memory_snapshot_id,
            candidates=[candidate_id],
        )
        self._manifests[manifest.release_id] = manifest
        self._persist("manifest", manifest.release_id, manifest.model_dump())
        # M10：发布写入 NS_RELEASES 记录（审计可查，跨实例存活）
        await self._memory_store.put(
            MemoryRecord(
                id=manifest.release_id,
                kind=MemoryKind.RULE,
                namespace=NS_RELEASES,
                content={
                    "release_id": manifest.release_id,
                    "candidates": manifest.candidates,
                    "memory_snapshot_id": manifest.memory_snapshot_id,
                    "created_at": manifest.created_at,
                },
            )
        )
        return manifest

    async def rollback(self, release_id: str, *, reason: str) -> ReleaseManifest:
        manifest = self._manifests.get(release_id)
        if manifest is None:
            raise KeyError(f"release {release_id} 不存在")
        for cid in manifest.candidates:
            candidate = self._candidates.get(cid)
            if candidate and candidate.candidate_type == "memory":
                memory_id = candidate.payload.get("memory_id")
                if memory_id:
                    await self._memory_publisher.invalidate(memory_id, reason=reason)
        inverse = ReleaseManifest(
            memory_snapshot_id=manifest.memory_snapshot_id,
            candidates=[],
            rollback_of=release_id,
            note=f"rollback: {reason}",
        )
        self._manifests[inverse.release_id] = inverse
        self._persist("manifest", inverse.release_id, inverse.model_dump())
        return inverse

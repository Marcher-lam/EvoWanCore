"""EvolutionController — 候选→评测→发布→回滚（spec §6，GEPA 式门禁）。

发布门槛：improved 且无回归。回滚生成反向 manifest，记忆候选走失效链。
候选可以修改产品，但不能修改自己的验收标准并据此证明通过。
"""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

from agent_core.memory.store import MemoryStore
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


class EvolutionController:
    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        memory_publisher: MemoryPublisher,
    ) -> None:
        self._memory_store = memory_store
        self._memory_publisher = memory_publisher
        self._candidates: dict[str, ExperienceCandidate] = {}
        self._manifests: dict[str, ReleaseManifest] = {}

    def submit(self, candidate: ExperienceCandidate) -> str:
        self._candidates[candidate.id] = candidate
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
            raise PublishDenied("未达发布门槛: improved=False（候选未优于父版本）")
        if report.regression_violations:
            raise PublishDenied(
                f"未达发布门槛: regression 回归 {report.regression_violations}"
                "（新知识不得破坏旧能力）"
            )
        manifest = ReleaseManifest(
            models=models or {},
            capabilities=capabilities or {},
            memory_snapshot_id=memory_snapshot_id,
            candidates=[candidate_id],
        )
        self._manifests[manifest.release_id] = manifest
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
        return inverse

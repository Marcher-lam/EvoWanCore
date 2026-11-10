"""MemoryWriter / Consolidator / Publisher — 按主张验证的写入-冲突-发布链（spec §5.4）。

铁律：propose 永远产出 CANDIDATE；任务整体 PASS 不为轨迹中每条结论背书
——claims ⊆ evidence_checked 才算主张有据；同条件反结论 → 双双 DISPUTED，
禁止覆盖。
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from agent_core.memory.record import (
    LifecycleState,
    MemoryKind,
    MemoryRecord,
    VerificationState,
    lifecycle_transition,
    verify_transition,
)
from agent_core.memory.store import MemoryStore


class ValidationReport(BaseModel):
    valid: bool
    reasons: list[str] = []


class MemoryWriter(Protocol):
    async def propose(
        self,
        kind: MemoryKind,
        content: dict,
        applicability: dict,
        evidence_refs: list[str],
        source_refs: list[str] | None = None,
    ) -> MemoryRecord: ...

    async def validate(self, record: MemoryRecord) -> ValidationReport: ...

    async def commit(self, record: MemoryRecord) -> MemoryRecord: ...


class DefaultMemoryWriter:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def propose(
        self,
        kind: MemoryKind,
        content: dict,
        applicability: dict,
        evidence_refs: list[str],
        source_refs: list[str] | None = None,
        environment_version: str | None = None,
    ) -> MemoryRecord:
        epistemic = "observed" if evidence_refs else "hypothesized"
        return MemoryRecord(
            kind=kind,
            namespace=self._namespace_for(kind),
            content=content,
            applicability=applicability,
            evidence_refs=evidence_refs,
            source_refs=source_refs or [],
            epistemic_status=epistemic,
            environment_version=environment_version,
        )

    async def validate(self, record: MemoryRecord) -> ValidationReport:
        claims = set(record.content.get("claims", []))
        checked = set(record.content.get("evidence_checked", []))
        unsupported = claims - checked
        if unsupported:
            return ValidationReport(
                valid=False,
                reasons=[f"主张缺乏证据覆盖: {', '.join(sorted(unsupported))}"],
            )
        if record.verification is VerificationState.VERIFIED and not record.evidence_refs:
            return ValidationReport(valid=False, reasons=["VERIFIED 记录必须携带证据引用"])
        return ValidationReport(valid=True)

    async def commit(self, record: MemoryRecord) -> MemoryRecord:
        await self._store.put(record)
        return await self._store.get(record.id) or record

    @staticmethod
    def _namespace_for(kind: MemoryKind) -> str:
        return {
            MemoryKind.RULE: "rules",
            MemoryKind.SKILL_REF: "skills",
            MemoryKind.FAILURE_PATTERN: "failures",
            MemoryKind.OBSERVATION: "coverage",
            MemoryKind.HYPOTHESIS: "gaps",
            MemoryKind.PROFILE: "profiles",
        }[kind]


class MemoryConflict(BaseModel):
    record_a_id: str
    record_b_id: str
    dimension: str
    subject: str = ""


class ConflictResolution(BaseModel):
    action: Literal["split", "dispute"]
    record_a_id: str
    record_b_id: str
    contrast_task_hint: dict = {}
    note: str = ""


class MemoryConsolidator(Protocol):
    async def detect_conflicts(self, records: list[MemoryRecord]) -> list[MemoryConflict]: ...

    async def resolve(self, conflict: MemoryConflict) -> ConflictResolution: ...


class DefaultConsolidator:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def detect_conflicts(self, records: list[MemoryRecord]) -> list[MemoryConflict]:
        conflicts: list[MemoryConflict] = []
        for i, a in enumerate(records):
            for b in records[i + 1 :]:
                if a.id == b.id:
                    continue
                if a.applicability != b.applicability:
                    continue
                if a.content.get("subject") != b.content.get("subject"):
                    continue
                if a.content.get("conclusion") != b.content.get("conclusion"):
                    conflicts.append(
                        MemoryConflict(
                            record_a_id=a.id,
                            record_b_id=b.id,
                            dimension="conclusion",
                            subject=str(a.content.get("subject", "")),
                        )
                    )
        return conflicts

    async def resolve(self, conflict: MemoryConflict) -> ConflictResolution:
        a = await self._store.get(conflict.record_a_id)
        b = await self._store.get(conflict.record_b_id)
        if a is None or b is None:
            raise KeyError("冲突记录不存在")
        if a.applicability != b.applicability:
            # 条件不同 → 拆成两条条件规则，各自保留
            return ConflictResolution(
                action="split",
                record_a_id=a.id,
                record_b_id=b.id,
                note="适用条件不同，拆分保留",
            )
        # 条件相同结论相反：双双 DISPUTED，触发 contrast 练习，禁止覆盖
        # （幂等：已 DISPUTED 的记录重复进入冲突集时不再转移）
        for rec in (a, b):
            if rec.verification is VerificationState.DISPUTED:
                continue
            disputed = verify_transition(rec, VerificationState.DISPUTED)
            await self._store.put(disputed)
        return ConflictResolution(
            action="dispute",
            record_a_id=a.id,
            record_b_id=b.id,
            contrast_task_hint={
                "family": "contrast",
                "subject": conflict.subject,
                "reason": "same_conditions_opposite_conclusions",
            },
        )


class MemoryPublisher(Protocol):
    async def publish(self, record_id: str, verifier_report: dict) -> MemoryRecord: ...

    async def invalidate(self, record_id: str, reason: str) -> None: ...


class PublishRefused(Exception):
    """验收报告未通过，发布被拒绝。"""


class VersionedPublisher:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def publish(self, record_id: str, verifier_report: dict) -> MemoryRecord:
        record = await self._store.get(record_id)
        if record is None:
            raise KeyError(f"memory {record_id} 不存在")
        if verifier_report.get("passed") is False:
            raise PublishRefused("验收报告未通过：候选记忆不得晋级 VERIFIED")
        verified = verify_transition(record, VerificationState.VERIFIED, evidence_ok=True)
        with_report = verified.model_copy(
            update={"content": {**verified.content, "verification_report": verifier_report}}
        )
        await self._store.put(with_report)
        return await self._store.get(record_id) or with_report

    async def invalidate(self, record_id: str, reason: str) -> None:
        record = await self._store.get(record_id)
        if record is None:
            raise KeyError(f"memory {record_id} 不存在")
        expired = lifecycle_transition(record, LifecycleState.EXPIRED)
        annotated = expired.model_copy(
            update={"content": {**expired.content, "invalidated_because": reason}}
        )
        await self._store.put(annotated)
        # 沿依赖传播：依赖此记录的结论自动进入待复核
        for ns in ("rules", "skills", "failures", "gaps", "coverage", "profiles"):
            for other in await self._store.query(ns, {}):
                if record_id in other.dependency_refs and other.verification not in (
                    VerificationState.DISPUTED,
                    VerificationState.REJECTED,
                ):
                    disputed = verify_transition(other, VerificationState.DISPUTED)
                    await self._store.put(disputed)

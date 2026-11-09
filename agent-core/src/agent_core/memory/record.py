"""MemoryRecord — 认知记忆的最小单元，验证态 × 生命周期双状态机（spec §5.4）。

epistemic_status 区分观察/假设/已验证主张；verification 是社会性验证结论；
lifecycle 是版本生命周期。三个维度不得混为一个 confidence 数字。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

EpistemicStatus = Literal["observed", "hypothesized", "verified"]


class MemoryKind(StrEnum):
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    RULE = "rule"
    SKILL_REF = "skill_ref"
    FAILURE_PATTERN = "failure_pattern"
    PROFILE = "profile"


class VerificationState(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    REJECTED = "rejected"


class LifecycleState(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    TOMBSTONED = "tombstoned"


class IllegalMemoryTransition(Exception):
    """记忆状态机守卫拒绝的转移。"""


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    revision: int = 1
    schema_version: int = 1
    kind: MemoryKind
    namespace: str
    owner: str = "system"
    access_policy: dict = {}
    content: dict
    artifact_refs: list[str] = []
    source_refs: list[str] = []
    evidence_refs: list[str] = []
    epistemic_status: EpistemicStatus = "observed"
    applicability: dict = {}
    environment_version: str | None = None
    valid_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    dependency_refs: list[str] = []
    verification: VerificationState = VerificationState.CANDIDATE
    lifecycle: LifecycleState = LifecycleState.ACTIVE


_VERIFY_TRANSITIONS: dict[VerificationState, frozenset[VerificationState]] = {
    VerificationState.CANDIDATE: frozenset(
        {VerificationState.VERIFIED, VerificationState.DISPUTED, VerificationState.REJECTED}
    ),
    VerificationState.VERIFIED: frozenset(
        {VerificationState.DISPUTED, VerificationState.REJECTED}
    ),
    VerificationState.DISPUTED: frozenset(
        {VerificationState.VERIFIED, VerificationState.REJECTED}
    ),
    VerificationState.REJECTED: frozenset(),
}

_LIFECYCLE_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.ACTIVE: frozenset(
        {LifecycleState.SUPERSEDED, LifecycleState.EXPIRED, LifecycleState.TOMBSTONED}
    ),
    LifecycleState.SUPERSEDED: frozenset({LifecycleState.TOMBSTONED}),
    LifecycleState.EXPIRED: frozenset({LifecycleState.TOMBSTONED}),
    LifecycleState.TOMBSTONED: frozenset(),
}


def verify_transition(
    record: MemoryRecord, target: VerificationState, *, evidence_ok: bool = False
) -> MemoryRecord:
    if target not in _VERIFY_TRANSITIONS[record.verification]:
        raise IllegalMemoryTransition(
            f"非法验证转移: {record.verification} → {target}"
        )
    if target is VerificationState.VERIFIED:
        if not record.evidence_refs:
            raise IllegalMemoryTransition("VERIFIED 拒绝：evidence_refs 为空，无证据不得晋级")
        if not evidence_ok:
            raise IllegalMemoryTransition("VERIFIED 拒绝：调用方未确认证据（evidence_ok=False）")
    return record.model_copy(
        update={
            "verification": target,
            "epistemic_status": "verified"
            if target is VerificationState.VERIFIED
            else record.epistemic_status,
        }
    )


def lifecycle_transition(
    record: MemoryRecord, target: LifecycleState, *, by: str | None = None
) -> MemoryRecord:
    if target not in _LIFECYCLE_TRANSITIONS[record.lifecycle]:
        raise IllegalMemoryTransition(f"非法生命周期转移: {record.lifecycle} → {target}")
    if target is LifecycleState.SUPERSEDED and not by:
        raise IllegalMemoryTransition("SUPERSEDED 必须携带 superseded_by（被哪条替代）")
    return record.model_copy(
        update={"lifecycle": target, "superseded_by": by or record.superseded_by}
    )

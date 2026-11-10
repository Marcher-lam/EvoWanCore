"""CurriculumMemoryView / Assembler — 课程控制面的决策视图（spec §5.4，round-9 MVP 子集）。

课程生成器站在"能力前沿"上出题，不站在"记忆堆"上回忆：
frontier / gaps / rules / skills / failures 是压缩后的中间视图，不是底层检索。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agent_core.memory.record import (
    LifecycleState,
    MemoryRecord,
    VerificationState,
)
from agent_core.memory.store import NS_FAILURES, NS_RULES, NS_SKILLS, MemoryStore
from pydantic import BaseModel, Field

from agent_explore.gaps import Gap, GapLedger, GapState, GapType

MASTERED_RATE = 0.8
MASTERED_MIN_RUNS = 3
SHAKY_MIN_RATE = 0.3


class CapabilityStatus(BaseModel):
    capability_id: str
    label: str = ""
    confidence: float = 0.0
    evidence_count: int = 0
    last_seen_at: datetime | None = None


class CapabilityFrontier(BaseModel):
    mastered: list[CapabilityStatus] = []
    shaky: list[CapabilityStatus] = []
    missing: list[str] = []
    blocked: list[str] = []


class CurriculumMemoryView(BaseModel):
    mode: str = "broad"
    session_state: dict = {}
    frontier: CapabilityFrontier = Field(default_factory=CapabilityFrontier)
    gaps: list[Gap] = []
    rules: list[MemoryRecord] = []
    skills: list[MemoryRecord] = []
    failures: list[MemoryRecord] = []
    signals: list[dict] = []


class CurriculumMemoryAssembler(Protocol):
    async def assemble(
        self,
        *,
        mode: str,
        memory_store: MemoryStore,
        gap_ledger: GapLedger,
        recent_outcomes: list[dict],
    ) -> CurriculumMemoryView: ...


def _active_verified(records: list[MemoryRecord]) -> list[MemoryRecord]:
    return [
        r
        for r in records
        if r.verification is VerificationState.VERIFIED
        and r.lifecycle is LifecycleState.ACTIVE
    ]


class DefaultAssembler:
    async def assemble(
        self,
        *,
        mode: str,
        memory_store: MemoryStore,
        gap_ledger: GapLedger,
        recent_outcomes: list[dict],
    ) -> CurriculumMemoryView:
        skills = _active_verified(await memory_store.query(NS_SKILLS, {}))
        rules = _active_verified(await memory_store.query(NS_RULES, {}))
        failures = _active_verified(await memory_store.query(NS_FAILURES, {}))
        gaps = await gap_ledger.open_gaps({})

        frontier = CapabilityFrontier()
        for s in skills:
            cap = str(s.content.get("capability", s.id))
            rate = float(s.content.get("success_rate", 0.0))
            runs = int(s.content.get("runs", 0))
            status = CapabilityStatus(
                capability_id=cap, label=cap, confidence=rate, evidence_count=runs
            )
            if rate >= MASTERED_RATE and runs >= MASTERED_MIN_RUNS:
                frontier.mastered.append(status)
            elif rate >= SHAKY_MIN_RATE:
                frontier.shaky.append(status)

        for gap in gaps:
            caps = gap.affected_capabilities or [gap.title]
            if gap.gap_type is GapType.SKILL:
                frontier.missing.extend(caps)
            elif gap.gap_type is GapType.AUTHORIZATION:
                frontier.blocked.extend(caps)

        signals: list[dict] = []
        for shaky in frontier.shaky:
            signals.append(
                {"code": f"FOCUS_SHAKY:{shaky.capability_id}", "score": 1 - shaky.confidence}
            )
        for cap in frontier.missing:
            signals.append({"code": f"EXPAND_MISSING:{cap}", "score": 1.0})
        for f in failures[:5]:
            signals.append(
                {"code": f"REVISIT_FAILURE:{f.content.get('subject', f.id)}", "score": 0.8}
            )

        session_state = {
            "recent_count": len(recent_outcomes),
            "last_status": recent_outcomes[-1]["status"] if recent_outcomes else None,
        }
        return CurriculumMemoryView(
            mode=mode,
            session_state=session_state,
            frontier=frontier,
            gaps=[g for g in gaps if g.status not in (GapState.RESOLVED, GapState.WONT_FIX)],
            rules=rules,
            skills=skills,
            failures=failures,
            signals=signals,
        )

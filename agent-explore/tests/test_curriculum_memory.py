"""T18 课程记忆视图与组装器：前沿分类、候选/过期排除、信号合成。"""

from pathlib import Path

import pytest
from agent_core.memory.record import (
    LifecycleState,
    MemoryKind,
    MemoryRecord,
    VerificationState,
    verify_transition,
)
from agent_core.memory.store import NS_FAILURES, NS_RULES, NS_SKILLS, SqliteMemoryStore
from agent_explore.curriculum_memory import (
    CapabilityFrontier,
    CurriculumMemoryAssembler,
    CurriculumMemoryView,
    DefaultAssembler,
)
from agent_explore.gaps import Gap, GapType, SqliteGapLedger


@pytest.fixture
async def env(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "mem.db")
    ledger = SqliteGapLedger(tmp_path / "gaps.db")
    return store, ledger


def _skill(capability: str, success_rate: float, runs: int) -> MemoryRecord:
    return MemoryRecord(
        kind=MemoryKind.SKILL_REF,
        namespace=NS_SKILLS,
        content={"capability": capability, "success_rate": success_rate, "runs": runs},
        evidence_refs=[f"evi_{capability}"],
        applicability={"app": "workbench"},
    )


async def _publish(store: SqliteMemoryStore, rec: MemoryRecord) -> None:
    await store.put(rec)
    current = await store.get(rec.id)
    await store.put(verify_transition(current, VerificationState.VERIFIED, evidence_ok=True))


async def test_frontier_classification_thresholds(env) -> None:
    store, ledger = env
    await _publish(store, _skill("export", 0.95, 5))  # mastered: ≥0.8 且 runs≥3
    await _publish(store, _skill("parse", 0.55, 4))  # shaky: 0.3–0.8
    await ledger.record(
        Gap(gap_type=GapType.SKILL, title="不会 OCR 修正", affected_capabilities=["ocr_fix"])
    )
    await ledger.record(
        Gap(
            gap_type=GapType.AUTHORIZATION,
            title="无权访问财务库",
            affected_capabilities=["fin_read"],
        )
    )

    assembler: CurriculumMemoryAssembler = DefaultAssembler()
    view = await assembler.assemble(
        mode="broad", memory_store=store, gap_ledger=ledger, recent_outcomes=[]
    )
    assert isinstance(view, CurriculumMemoryView)
    frontier: CapabilityFrontier = view.frontier
    assert [c.capability_id for c in frontier.mastered] == ["export"]
    assert [c.capability_id for c in frontier.shaky] == ["parse"]
    assert frontier.missing == ["ocr_fix"]
    assert frontier.blocked == ["fin_read"]


async def test_candidate_and_stale_excluded_from_view(env) -> None:
    store, ledger = env
    # 候选规则（未验证）——不进课程视图
    await store.put(
        MemoryRecord(
            kind=MemoryKind.RULE,
            namespace=NS_RULES,
            content={"statement": "候选规则"},
            evidence_refs=["evi"],
        )
    )
    # 已验证但被取代的技能——不进课程视图
    old = _skill("export", 0.9, 4)
    await _publish(store, old)
    current = await store.get(old.id)
    await store.put(
        current.model_copy(
            update={"lifecycle": LifecycleState.SUPERSEDED, "superseded_by": "mem_new"}
        )
    )
    # 已验证且活跃的失败模式——应进视图
    await _publish(
        store,
        MemoryRecord(
            kind=MemoryKind.FAILURE_PATTERN,
            namespace=NS_FAILURES,
            content={"subject": "空导出", "conclusion": True},
            evidence_refs=["evi_f"],
        ),
    )

    view = await DefaultAssembler().assemble(
        mode="broad", memory_store=store, gap_ledger=ledger, recent_outcomes=[]
    )
    assert view.rules == []  # 候选被排除
    assert view.skills == []  # 被取代被排除
    assert len(view.failures) == 1


async def test_signals_summarize_frontier(env) -> None:
    store, ledger = env
    await _publish(store, _skill("parse", 0.4, 3))
    await ledger.record(
        Gap(gap_type=GapType.SKILL, title="缺 OCR", affected_capabilities=["ocr_fix"])
    )

    view = await DefaultAssembler().assemble(
        mode="deep", memory_store=store, gap_ledger=ledger,
        recent_outcomes=[
            {"task_id": "p1", "status": "passed"},
            {"task_id": "p2", "status": "failed"},
        ],
    )
    codes = [s["code"] for s in view.signals]
    assert "FOCUS_SHAKY:parse" in codes
    assert "EXPAND_MISSING:ocr_fix" in codes
    assert view.session_state["recent_count"] == 2
    assert view.session_state["last_status"] == "failed"
    assert view.mode == "deep"

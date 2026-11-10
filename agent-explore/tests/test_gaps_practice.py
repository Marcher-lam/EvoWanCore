"""T17 GapLedger + 练习对象模型：授权缺口不自动绕过、练习必带验收。"""

from pathlib import Path

import pytest
from agent_explore.gaps import Gap, GapLedger, GapState, GapType, SqliteGapLedger
from agent_explore.practice import (
    AcceptanceSpec,
    DiagnosisType,
    PracticeTask,
    PracticeWave,
    TaskFamily,
)
from pydantic import ValidationError


def _gap(
    gap_type: GapType = GapType.KNOWLEDGE, status: GapState = GapState.DISCOVERED, **over
) -> Gap:
    base = dict(
        gap_type=gap_type,
        title="不知道导出的前置条件",
        description="导出按钮灰置原因未知",
        severity=0.7,
        recurrence=3,
        status=status,
        reproduce_conditions={"page": "export"},
        evidence_refs=["evi_1"],
        closure_condition="确认灰置条件并可复现",
        affected_capabilities=["export_document"],
    )
    base.update(over)
    return Gap(**base)


@pytest.fixture
def ledger(tmp_path: Path) -> SqliteGapLedger:
    return SqliteGapLedger(tmp_path / "gaps.db")


async def test_record_and_open_gaps(ledger: GapLedger) -> None:
    gid = await ledger.record(_gap())
    opened = await ledger.open_gaps({})
    assert [g.id for g in opened] == [gid]
    await ledger.close(gid, resolution="已验证：需先选择导出范围")
    assert await ledger.open_gaps({}) == []


async def test_auth_gap_never_auto_actionable(ledger: GapLedger) -> None:
    # 即使调用方硬给 ACTIONABLE，授权缺口也固定 WAITING_EXTERNAL
    gid = await ledger.record(
        _gap(gap_type=GapType.AUTHORIZATION, status=GapState.ACTIONABLE, title="无权访问财务系统")
    )
    gap = [g for g in await ledger.open_gaps({}) if g.id == gid][0]
    assert gap.status is GapState.WAITING_EXTERNAL

    # update 也堵死自动转 ACTIONABLE 的路径
    await ledger.update(gap.model_copy(update={"status": GapState.TRIAGED}))
    again = [g for g in await ledger.open_gaps({}) if g.id == gid][0]
    assert again.status is GapState.WAITING_EXTERNAL

    # 解锁只能来自外部：显式声明 unlocked
    await ledger.update(
        again.model_copy(update={"status": GapState.ACTIONABLE, "external_unlock": True})
    )
    unlocked = [g for g in await ledger.open_gaps({}) if g.id == gid][0]
    assert unlocked.status is GapState.ACTIONABLE


async def test_close_missing_gap_raises(ledger: GapLedger) -> None:
    with pytest.raises(KeyError):
        await ledger.close("gap_nope", resolution="x")


# ---------- 练习对象 ----------


def test_practice_task_requires_acceptance() -> None:
    with pytest.raises(ValidationError):
        PracticeTask(
            family=TaskFamily.PREREQUISITE,
            title="识别导出按钮状态",
            objective={"what": "确认按钮可用条件"},
        )


def test_practice_task_with_acceptance() -> None:
    task = PracticeTask(
        family=TaskFamily.CONTRAST,
        title="对比灰置与无权限",
        objective={},
        acceptance=AcceptanceSpec(
            verifier_profile="workbench_programmatic",
            required_evidence=["screenshot_diff"],
            pass_conditions=[{"check": "states_distinguished"}],
            fail_conditions=[],
        ),
        priority=0.8,
        difficulty=0.4,
    )
    assert task.acceptance.verifier_profile == "workbench_programmatic"
    assert task.priority == 0.8


def test_practice_wave_shape() -> None:
    wave = PracticeWave(
        shared_memory_snapshot_id="snap_1",
        task_ids=["pt_1", "pt_2"],
        parallelism=4,
    )
    assert wave.merge_policy == "sequential"
    assert wave.wave_id.startswith("wave_")


def test_diagnosis_types_complete() -> None:
    assert {d.value for d in DiagnosisType} == {
        "missing_prerequisite",
        "wrong_causal_model",
        "overgeneralized_rule",
        "verification_gap",
        "fragile_success",
    }

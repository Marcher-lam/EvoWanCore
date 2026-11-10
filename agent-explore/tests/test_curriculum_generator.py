"""T19 LLM 课程生成器：畸形输出重试、诊断映射任务族、预算停止。"""

import json

import pytest
from agent_core.models import (
    FakeModelAdapter,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
)
from agent_explore.curriculum import (
    BudgetMeter,
    CurriculumDecision,
    CurriculumGenerationError,
    ExplorationBudget,
    LLMCurriculumGenerator,
)
from agent_explore.curriculum_memory import CurriculumMemoryView
from agent_explore.practice import DiagnosisType, TaskFamily


def _view() -> CurriculumMemoryView:
    return CurriculumMemoryView(mode="broad")


def _valid_plan_json(snapshot_id: str = "snap_1") -> str:
    task = {
        "family": "prerequisite",
        "title": "识别导出按钮",
        "objective": {"what": "确认按钮可用"},
        "acceptance": {
            "verifier_profile": "workbench_programmatic",
            "required_evidence": ["screenshot"],
            "pass_conditions": [{"check": "button_found"}],
            "fail_conditions": [],
        },
    }
    return json.dumps(
        {
            "wave": {
                "shared_memory_snapshot_id": snapshot_id,
                "task_ids": [],
                "parallelism": 2,
            },
            "tasks": [task],
        }
    )


def _generator(responses: list[InferResponse]) -> tuple[LLMCurriculumGenerator, FakeModelAdapter]:
    caps = ModelCapabilities(model_id="m-cur", inputs={Modality.TEXT}, tools={"structured_action"})
    fake = FakeModelAdapter(caps, responses)
    gw = ModelGateway()
    gw.register(fake)
    return LLMCurriculumGenerator(gateway=gw, model_id="m-cur"), fake


async def test_malformed_plan_retried_once_then_succeeds() -> None:
    gen, fake = _generator(
        [
            InferResponse(text="这不是 JSON"),
            InferResponse(text=_valid_plan_json()),
        ]
    )
    plan = await gen.plan_wave(
        _view(), mode="broad", budget=None, shared_memory_snapshot_id="snap_1"
    )
    assert plan.wave.shared_memory_snapshot_id == "snap_1"
    assert plan.tasks[0].family is TaskFamily.PREREQUISITE
    assert len(fake.recorded_requests) == 2  # 重试了一次
    # 重试请求携带了校验错误信息
    assert "校验" in fake.recorded_requests[1].messages[-1]["content"]


async def test_malformed_plan_twice_raises() -> None:
    gen, _ = _generator([InferResponse(text="bad1"), InferResponse(text="bad2")])
    with pytest.raises(CurriculumGenerationError):
        await gen.plan_wave(
            _view(), mode="broad", budget=None, shared_memory_snapshot_id="snap_1"
        )


async def test_refine_corrects_family_by_diagnosis() -> None:
    # LLM 给了错误任务族（stress），生成器必须按诊断映射纠正为 prerequisite
    task_json = json.dumps(
        {
            "family": "stress",
            "title": "补前置",
            "objective": {},
            "acceptance": {
                "verifier_profile": "p",
                "required_evidence": [],
                "pass_conditions": [],
                "fail_conditions": [],
            },
        }
    )
    gen, fake = _generator([InferResponse(text=task_json)])
    prev = {
        "id": "pt_prev",
        "family": "target_attempt",
        "title": "直接导出",
        "objective": {},
    }
    refined = await gen.refine_after_result(
        prev_task=prev,
        outcome={"status": "failed"},
        diagnosis={"type": DiagnosisType.MISSING_PREREQUISITE.value, "note": "缺选择区"},
        view=_view(),
    )
    assert refined.family is TaskFamily.PREREQUISITE  # 被映射纠正
    all_msgs = " ".join(m["content"] for m in fake.recorded_requests[0].messages)
    assert DiagnosisType.MISSING_PREREQUISITE.value in all_msgs


def test_budget_meter_and_stop() -> None:
    budget = ExplorationBudget(max_actions=3)
    meter = BudgetMeter(budget)
    status = meter.consume(actions=2)
    assert status.exhausted is False
    decision: CurriculumDecision = LLMCurriculumGenerator._stop_decision(status)
    assert decision.action == "continue"

    status2 = meter.consume(actions=2)
    assert status2.exhausted is True
    decision2 = LLMCurriculumGenerator._stop_decision(status2)
    assert decision2.action == "stop"
    assert "budget" in decision2.reason.lower() or "预算" in decision2.reason


def test_budget_unlimited_dim_never_exhausts() -> None:
    meter = BudgetMeter(ExplorationBudget())  # 全部 None = 不限
    assert meter.consume(tokens=10**9, actions=10**6, cost=999.0).exhausted is False


async def test_plan_wave_receives_practice_pool() -> None:
    """I2: 课程提示必须携带可练习任务池，真模型才能出语料内任务。"""
    gen, fake = _generator([InferResponse(text=_valid_plan_json())])
    pool = [
        {"id": "convert_doc_3_3", "dimension": "convert", "doc_id": "doc_3", "goal": "无损转换"},
        {"id": "extract_doc_1_1", "dimension": "extract", "doc_id": "doc_1", "goal": "抽取字段"},
    ]
    await gen.plan_wave(_view(), mode="broad", budget=None,
                        shared_memory_snapshot_id="snap_9", practice_pool=pool)
    content = " ".join(m["content"] for m in fake.recorded_requests[0].messages)
    assert "convert_doc_3_3" in content
    assert "extract_doc_1_1" in content


# ---------- T13：gap→contrast 课程闭环（覆盖校验） ----------


def _view_with_gap(dim: str = "extract") -> CurriculumMemoryView:
    from agent_explore.gaps import Gap, GapType

    return CurriculumMemoryView(
        mode="broad",
        gaps=[
            Gap(
                gap_type=GapType.KNOWLEDGE,
                title=f"{dim} 练习未通过",
                affected_capabilities=[f"{dim}_doc_3"],
            )
        ],
    )


def _plan_with_ids(snapshot_id: str, ids: list[str]) -> str:
    tasks = []
    for i in ids:
        tasks.append(
            {
                "id": i,
                "family": "contrast",
                "title": f"针对性练习 {i}",
                "objective": {"what": "覆盖 gap 维度"},
                "acceptance": {
                    "verifier_profile": "p",
                    "required_evidence": [],
                    "pass_conditions": [{"check": "done"}],
                    "fail_conditions": [],
                },
            }
        )
    return json.dumps(
        {"wave": {"shared_memory_snapshot_id": snapshot_id, "task_ids": [], "parallelism": 2},
         "tasks": tasks}
    )


async def test_gap_drives_wave_coverage() -> None:
    """存在 open gap：第一次计划不含 gap 维度 → 校验错误重试；第二次含 → 通过。"""
    gen, fake = _generator(
        [
            InferResponse(text=_plan_with_ids("snap_1", ["export_doc_9"])),
            InferResponse(text=_plan_with_ids("snap_1", ["extract_doc_9", "export_doc_9"])),
        ]
    )
    plan = await gen.plan_wave(
        _view_with_gap(), mode="broad", budget=None, shared_memory_snapshot_id="snap_1"
    )
    assert any(t.id.startswith("extract") for t in plan.tasks)
    assert len(fake.recorded_requests) == 2, "gap 未覆盖应触发一次重试"
    assert "gap" in fake.recorded_requests[1].messages[-1]["content"].lower()


async def test_gap_coverage_twice_misses_raises() -> None:
    """两次都不覆盖 gap 维度 → CurriculumGenerationError。"""
    gen, _ = _generator(
        [
            InferResponse(text=_plan_with_ids("snap_1", ["export_doc_1"])),
            InferResponse(text=_plan_with_ids("snap_1", ["export_doc_2"])),
        ]
    )
    with pytest.raises(CurriculumGenerationError, match="gap"):
        await gen.plan_wave(
            _view_with_gap(), mode="broad", budget=None, shared_memory_snapshot_id="snap_1"
        )

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

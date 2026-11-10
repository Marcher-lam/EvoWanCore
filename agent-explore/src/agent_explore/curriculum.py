"""CurriculumGenerator / 预算 — 课程控制面（spec §5.3，LLM 出题 + 校验门禁）。

输出不是"下一题建议"，而是带探索意图、验收规格的可运行学习计划。
LLM 输出畸形 → 携校验错误重试一次 → 再失败抛 CurriculumGenerationError。
任务族由诊断映射决定（DIAGNOSIS_FAMILY_MAP），不信任 LLM 的自选。
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal, Protocol

from agent_core.models import InferRequest, Modality, ModelGateway
from pydantic import BaseModel, ValidationError

from agent_explore.curriculum_memory import CurriculumMemoryView
from agent_explore.practice import (
    DIAGNOSIS_FAMILY_MAP,
    PracticeTask,
    PracticeWave,
    TaskFamily,
)


class CurriculumMode(StrEnum):
    BROAD = "broad"
    DEEP = "deep"
    MIXED = "mixed"


class CurriculumDecision(BaseModel):
    action: Literal["continue", "switch_mode", "stop", "escalate"]
    reason: str
    suggested_next_mode: CurriculumMode | None = None


class PracticeWavePlan(BaseModel):
    """plan_wave 的产物：波次 + 该波的任务定义（wave 只存 id，任务在这里）。"""

    wave: PracticeWave
    tasks: list[PracticeTask]

    @staticmethod
    def json_schema_hint() -> dict:
        return {"name": "PracticeWavePlan", "schema": PracticeWavePlan.model_json_schema()}


class ExplorationBudget(BaseModel):
    max_tokens: int | None = None
    max_actions: int | None = None
    wall_clock_s: int | None = None
    max_cost: float | None = None


class BudgetStatus(BaseModel):
    spent: dict = {}
    remaining: dict = {}
    exhausted: bool = False


class CurriculumGenerationError(Exception):
    """课程生成输出两轮校验失败。"""


def _consume_check(budget: ExplorationBudget, spent: dict) -> bool:
    checks = {
        "tokens": budget.max_tokens,
        "actions": budget.max_actions,
        "wall_clock_s": budget.wall_clock_s,
        "cost": budget.max_cost,
    }
    return any(limit is not None and spent.get(dim, 0) >= limit for dim, limit in checks.items())


class BudgetMeter:
    def __init__(self, budget: ExplorationBudget) -> None:
        self._budget = budget
        self._spent: dict = {}

    def consume(
        self,
        *,
        tokens: int = 0,
        actions: int = 0,
        cost: float = 0.0,
        wall_clock_s: float = 0.0,
    ) -> BudgetStatus:
        for dim, delta in {
            "tokens": tokens,
            "actions": actions,
            "cost": cost,
            "wall_clock_s": wall_clock_s,
        }.items():
            self._spent[dim] = self._spent.get(dim, 0) + delta
        remaining = {
            dim: (limit - self._spent.get(dim, 0)) if limit is not None else None
            for dim, limit in {
                "tokens": self._budget.max_tokens,
                "actions": self._budget.max_actions,
                "cost": self._budget.max_cost,
                "wall_clock_s": self._budget.wall_clock_s,
            }.items()
        }
        return BudgetStatus(
            spent=dict(self._spent),
            remaining=remaining,
            exhausted=_consume_check(self._budget, self._spent),
        )

    @property
    def status(self) -> BudgetStatus:
        return self.consume()

    @property
    def exhausted(self) -> bool:
        """与 AgentRuntime budget_gate 鸭型兼容（真实调用计数口径同样适用）。"""
        return self.status.exhausted


class CurriculumGenerator(Protocol):
    async def plan_wave(
        self,
        view: CurriculumMemoryView,
        mode: str,
        budget: ExplorationBudget | None,
        *,
        shared_memory_snapshot_id: str,
        practice_pool: list[dict] | None = None,
    ) -> PracticeWavePlan: ...

    async def refine_after_result(
        self,
        prev_task: dict,
        outcome: dict,
        diagnosis: dict,
        view: CurriculumMemoryView,
    ) -> PracticeTask | CurriculumDecision: ...

    async def should_stop(
        self, view: CurriculumMemoryView, budget_status: BudgetStatus
    ) -> CurriculumDecision: ...


class LLMCurriculumGenerator:
    def __init__(self, *, gateway: ModelGateway, model_id: str) -> None:
        self._gateway = gateway
        self._model_id = model_id

    async def _infer_validated(
        self, messages: list[dict], schema_hint: dict, result_cls, post_check=None
    ):
        """带校验-重试的 infer：畸形输出重试一次（附错误），再失败抛错。

        post_check: 结构合法后的语义校验（如 T13 gap 覆盖）；返回错误串或 None，
        非 None 同样走重试-失败路径。
        """
        request_msgs = list(messages)
        last_error = ""
        for _attempt in range(2):
            adapter = self._gateway.route({"inputs": [Modality.TEXT]})
            resp = await adapter.infer(
                InferRequest(
                    model_id=adapter.capabilities().model_id,
                    messages=request_msgs,
                    structured_output=schema_hint,
                )
            )
            try:
                parsed = result_cls.model_validate_json(resp.text)
                if post_check is not None:
                    semantic_error = post_check(parsed)
                    if semantic_error:
                        raise ValueError(semantic_error)  # noqa: TRY301 — 语义校验走同款重试
                return parsed
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_error = f"{type(e).__name__}: {e}"
                request_msgs = list(messages) + [
                    {
                        "role": "user",
                        "content": f"上一次输出未通过校验（{last_error}）。"
                        "请严格按 schema 输出合法 JSON，不要输出其他内容。",
                    }
                ]
        raise CurriculumGenerationError(f"课程输出两轮校验失败: {last_error}")

    async def plan_wave(
        self,
        view: CurriculumMemoryView,
        mode: str,
        budget: ExplorationBudget | None,
        *,
        shared_memory_snapshot_id: str,
        practice_pool: list[dict] | None = None,
    ) -> PracticeWavePlan:
        pool_hint = ""
        if practice_pool:
            ids = ", ".join(t["id"] for t in practice_pool[:20])
            pool_hint = (
                f" Practiceable tasks (task ids MUST come from this pool; unknown ids "
                f"cannot be verified and will fail): {ids}."
            )
        gaps_hint = ""
        if view.gaps:
            affected = sorted(
                {c.split("_", 1)[0] for g in view.gaps for c in g.affected_capabilities}
            )
            gaps_hint = (
                f" OPEN GAPS (unpassed practice dimensions): {affected} — at least ONE "
                "task in this wave MUST target one of these gap dimensions."
            )
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are the curriculum planner for a document-workbench agent "
                    f"(mode={mode}). Design a practice wave. Signals: "
                    f"{json.dumps(view.signals)}. Frontier: "
                    f"{view.frontier.model_dump()}.{gaps_hint}{pool_hint} Produce ONE "
                    "PracticeWavePlan JSON: a wave (use the given snapshot id) plus 1-4 "
                    "tasks; every task MUST carry an acceptance spec."
                ),
            },
            {
                "role": "user",
                "content": f"shared_memory_snapshot_id={shared_memory_snapshot_id}",
            },
        ]

        class _WaveWithSnapshot(PracticeWavePlan):
            pass

        def _gap_coverage_check(plan: PracticeWavePlan) -> str | None:
            """T13：存在 open gap 时，波内任务必须覆盖至少一个 gap 的受影响维度。"""
            if not view.gaps:
                return None
            affected = {
                c.split("_", 1)[0]
                for g in view.gaps
                for c in g.affected_capabilities
            }
            wave_dims = {t.id.split("_", 1)[0] for t in plan.tasks}
            if wave_dims & affected:
                return None
            return (
                f"open gap 未被覆盖: gaps 需要维度 {sorted(affected)} 的针对性练习，"
                f"波内任务只覆盖 {sorted(wave_dims)}"
            )

        plan: PracticeWavePlan = await self._infer_validated(
            messages,
            PracticeWavePlan.json_schema_hint(),
            _WaveWithSnapshot,
            post_check=_gap_coverage_check,
        )
        # 快照 id 由运行时提供，不信任 LLM 回显
        plan = plan.model_copy(
            update={
                "wave": plan.wave.model_copy(
                    update={"shared_memory_snapshot_id": shared_memory_snapshot_id}
                )
            }
        )
        if not plan.tasks:
            raise CurriculumGenerationError("波次不含任何任务")
        plan.wave.task_ids = [t.id for t in plan.tasks]
        return plan

    async def refine_after_result(
        self,
        prev_task: dict,
        outcome: dict,
        diagnosis: dict,
        view: CurriculumMemoryView,
    ) -> PracticeTask | CurriculumDecision:
        diag_type = diagnosis.get("type", "")
        suggested = DIAGNOSIS_FAMILY_MAP.get(diag_type, TaskFamily.VARIANT)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the DRS practice designer. The previous practice failed or was "
                    f"fragile. Diagnosis: {json.dumps(diagnosis, ensure_ascii=False)}. "
                    f"Design ONE follow-up PracticeTask of family '{suggested.value}' "
                    "to target this weakness. Output the PracticeTask JSON with an "
                    "acceptance spec."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"prev_task={json.dumps(prev_task, ensure_ascii=False)} "
                    f"outcome={json.dumps(outcome, ensure_ascii=False)}"
                ),
            },
        ]
        task: PracticeTask = await self._infer_validated(
            messages,
            {"name": "PracticeTask", "schema": PracticeTask.model_json_schema()},
            PracticeTask,
        )
        # 任务族由诊断映射决定，纠正 LLM 的自选
        return task.model_copy(update={"family": suggested})

    async def should_stop(
        self, view: CurriculumMemoryView, budget_status: BudgetStatus
    ) -> CurriculumDecision:
        return self._stop_decision(budget_status)

    @staticmethod
    def _stop_decision(budget_status: BudgetStatus) -> CurriculumDecision:
        if budget_status.exhausted:
            return CurriculumDecision(
                action="stop", reason=f"budget exhausted (预算耗尽): {budget_status.spent}"
            )
        return CurriculumDecision(action="continue", reason="budget remains")

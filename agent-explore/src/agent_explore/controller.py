"""ExplorationController — BRS 波次 + DRS 深练（spec §6 探索闭环）。

BRS：should_stop? → plan_wave（共享快照）→ 并行执行 → 整波完成后按序合并
（propose→validate→冲突检查；冲突双双 DISPUTED + contrast 提示进入下一轮）。
DRS：refine(诊断) → 单任务执行 → 验证 → **先整合记忆再出下一题** → should_stop。
波内单任务异常收进 outcome，不取消姊妹任务。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Literal

from agent_core.memory.store import MemoryStore
from agent_core.memory.writer import (
    MemoryConsolidator,
    MemoryWriter,
)
from agent_core.runtime.driver import AgentSpec, Verifier
from agent_core.runtime.runtime import AgentRuntime
from agent_core.task import TaskSpec
from pydantic import BaseModel

from agent_explore.curriculum import (
    BudgetMeter,
    CurriculumDecision,
    CurriculumGenerator,
    CurriculumMode,
    ExplorationBudget,
)
from agent_explore.curriculum_memory import CurriculumMemoryAssembler
from agent_explore.gaps import Gap, GapLedger, GapType
from agent_explore.practice import PracticeTask, TaskFamily


class ExplorationPolicy(BaseModel):
    mode: CurriculumMode = CurriculumMode.BROAD
    budget: ExplorationBudget = ExplorationBudget()
    max_waves: int | None = 2
    max_deep: int = 3
    strategies: list[str] = ["breadth", "gap_driven", "counterexample"]


class PracticeOutcome(BaseModel):
    task_id: str
    title: str = ""
    family: str = "variant"
    status: Literal["passed", "failed", "error"] = "failed"
    evidence_refs: list[str] = []
    actions: list[dict] = []  # 动作轨迹（capability_id/arguments/status）——reflector 因果依据
    verifier_checks: list[dict] = []  # 验收反馈（含失败 detail）——环境反馈信号
    error: dict | None = None


class ExplorationReport(BaseModel):
    waves_executed: int = 0
    tasks_executed: int = 0
    tasks_passed: int = 0
    candidates_generated: int = 0
    candidates_published: int = 0
    gaps_opened: int = 0
    budget_spent: dict = {}
    conflict_hints: list[dict] = []


class _CallCursor:
    """对 CountingAdapter.calls 取增量（V2 §6.2 真实调用计量，替换 V1 占位计数）。

    app_context["model_counter"] 缺席（未接线计数适配器）时增量为 0——
    tokens 维度不计量，actions 维度不受影响。
    """

    def __init__(self, counter) -> None:
        self._counter = counter
        self._last = counter.calls if counter is not None else 0

    def take(self) -> int:
        if self._counter is None:
            return 0
        delta = self._counter.calls - self._last
        self._last = self._counter.calls
        return delta


def _diagnose(outcome: PracticeOutcome) -> dict:
    if outcome.status == "error":
        return {"type": "verification_gap", "note": outcome.error or "执行异常"}
    if outcome.status == "failed":
        # T14 维度感知：extract/export 失败多为错误因果模型（双栏/丢页陷阱）→
        # contrast 族针对性练习；其余失败保持缺前置技能诊断
        dimension = outcome.task_id.split("_", 1)[0]
        if dimension in ("extract", "export"):
            return {
                "type": "wrong_causal_model",
                "note": f"{dimension} 维度练习未过：疑似错误因果模型",
            }
        return {"type": "missing_prerequisite", "note": "练习未过验收"}
    if outcome.family == TaskFamily.TARGET_ATTEMPT.value:
        return {"type": "fragile_success", "note": "目标达成但可能脆弱"}
    return {"type": "overgeneralized_rule", "note": "成功，验证适用边界"}


class ExplorationController:
    def __init__(
        self,
        *,
        curriculum: CurriculumGenerator,
        assembler: CurriculumMemoryAssembler,
        writer: MemoryWriter,
        consolidator: MemoryConsolidator,
        memory_store: MemoryStore,
        gap_ledger: GapLedger,
    ) -> None:
        self._curriculum = curriculum
        self._assembler = assembler
        self._writer = writer
        self._consolidator = consolidator
        self._store = memory_store
        self._gaps = gap_ledger

    async def explore(
        self,
        target: TaskSpec,
        policy: ExplorationPolicy,
        verifier: Verifier,
        app_context: dict,
    ) -> ExplorationReport:
        report = ExplorationReport()
        meter = BudgetMeter(policy.budget)
        hints: list[dict] = []

        if policy.mode in (CurriculumMode.BROAD, CurriculumMode.MIXED):
            await self._brs(target, policy, verifier, app_context, meter, report, hints)

        if policy.mode in (CurriculumMode.DEEP, CurriculumMode.MIXED):
            app_context = {**app_context, "policy_max_deep": policy.max_deep}
            await self._drs(target, verifier, app_context, meter, report, hints)

        report.budget_spent = meter.status.spent
        return report

    # ---------- BRS ----------

    async def _brs(
        self,
        target: TaskSpec,
        policy: ExplorationPolicy,
        verifier: Verifier,
        app_context: dict,
        meter: BudgetMeter,
        report: ExplorationReport,
        hints: list[dict],
    ) -> None:
        max_waves = policy.max_waves or 2
        cursor = _CallCursor(app_context.get("model_counter"))
        for wave_n in range(max_waves):
            view = await self._assembler.assemble(
                mode="broad",
                memory_store=self._store,
                gap_ledger=self._gaps,
                recent_outcomes=[],
            )
            # 冲突提示作为课程输入（不选边站，交给 contrast 练习）
            view.signals = list(view.signals) + [
                {"code": h.get("code", "CONTRAST"), "score": 1.5, "hint": h} for h in hints
            ]
            decision = await self._curriculum.should_stop(view, meter.status)
            if decision.action == "stop":
                break

            # V1 快照语义：并行期间不写入，波次边界即快照边界
            snapshot_id = f"snap_{wave_n}"
            plan = await self._curriculum.plan_wave(
                view,
                "broad",
                policy.budget,
                shared_memory_snapshot_id=snapshot_id,
                practice_pool=app_context.get("practice_pool"),
            )

            outcomes = await self._run_wave(
                plan.tasks, verifier, app_context, parallelism=plan.wave.parallelism
            )
            report.waves_executed += 1
            report.tasks_executed += len(outcomes)
            report.tasks_passed += sum(1 for o in outcomes if o.status == "passed")
            report.gaps_opened += await self._record_gaps(plan.tasks, outcomes)

            new_hints, generated = await self._merge_outcomes(plan.tasks, outcomes, app_context)
            hints.extend(new_hints)
            report.conflict_hints = list(hints)
            report.candidates_generated += generated

            # 真实调用计量（§6.2）：本波 should_stop/plan/practice 的全部模型调用
            meter.consume(actions=len(plan.tasks), tokens=cursor.take())
            if meter.status.exhausted:
                break

    async def _record_gaps(
        self, tasks: list[PracticeTask], outcomes: list[PracticeOutcome]
    ) -> int:
        """I5: 失败/异常的练习 → GapLedger（gap-driven 课程的输入）。"""
        opened = 0
        for outcome in outcomes:
            if outcome.status == "passed":
                continue
            gap_type = GapType.KNOWLEDGE if outcome.status == "failed" else GapType.VERIFICATION
            await self._gaps.record(
                Gap(
                    gap_type=gap_type,
                    title=f"练习未通过: {outcome.title}",
                    description=f"family={outcome.family}, status={outcome.status}",
                    severity=0.6,
                    affected_capabilities=[outcome.task_id],
                    reproduce_conditions={"task_id": outcome.task_id},
                    evidence_refs=outcome.evidence_refs,
                    closure_condition="该练习通过程序化验收",
                )
            )
            opened += 1
        return opened

    async def _run_wave(
        self,
        tasks: list[PracticeTask],
        verifier: Verifier,
        app_context: dict,
        parallelism: int = 4,
    ) -> list[PracticeOutcome]:
        """并行执行（Semaphore 限并发）；单任务异常收进 outcome（status=error），不杀姊妹任务。"""
        gate = asyncio.Semaphore(max(1, parallelism))  # clamp 防 LLM 输出 0/负值死锁

        async def _one(task: PracticeTask) -> PracticeOutcome:
            async with gate:
                try:
                    return await self._run_practice(task, verifier, app_context)
                except Exception as e:  # noqa: BLE001 — 波内隔离
                    return PracticeOutcome(
                        task_id=task.id,
                        title=task.title,
                        family=task.family.value,
                        status="error",
                        error={"reason": f"{type(e).__name__}: {e}"},
                    )

        return list(await asyncio.gather(*(_one(t) for t in tasks)))

    async def _run_practice(
        self, task: PracticeTask, verifier: Verifier, app_context: dict
    ) -> PracticeOutcome:
        # 练习池上下文确定性合并：doc_id 等关键信息来自池（不信 LLM 回显）
        pool_entry = next(
            (p for p in app_context.get("practice_pool", []) if p.get("id") == task.id), {}
        )
        goal = {
            "what": task.title,
            "dimension": pool_entry.get("dimension"),
            "doc_id": pool_entry.get("doc_id"),
            **{k: v for k, v in task.objective.items() if k != "what"},
        }
        spec = TaskSpec(
            id=task.id,
            kind=f"practice:{task.family.value}",
            goal=goal,
            input_schema={},
            constraints=task.constraints,
            acceptance_criteria=task.acceptance.pass_conditions or [{"check": "practice_done"}],
        )
        runtime: AgentRuntime = app_context["runtime"]
        agent: AgentSpec = app_context["agent"]
        run = await runtime.submit(spec, agent)
        evidence_refs: list[list[str]] = []
        proposed: dict[str, dict] = {}
        actions: list[dict] = []
        verifier_checks: list[dict] = []
        async for e in runtime.events(run.id):
            if e.type == "ActionProposed":
                proposed[e.subject_id] = {
                    "capability_id": e.payload.get("capability_id"),
                    "arguments": e.payload.get("arguments", {}),
                }
            elif e.type in ("ActionSucceeded", "ActionFailed"):
                actions.append(
                    {
                        **proposed.get(e.subject_id, {}),
                        "status": "succeeded" if e.type == "ActionSucceeded" else "failed",
                    }
                )
            elif e.type == "RunVerified":
                verifier_checks = list(e.payload.get("checks", []))
            if e.type == "ActionSucceeded":
                evidence_refs.append(e.payload.get("evidence_refs", []))
        flat = [ref for group in evidence_refs for ref in group]
        status = "passed" if run.state.value == "succeeded" else "failed"
        return PracticeOutcome(
            task_id=task.id,
            title=task.title,
            family=task.family.value,
            status=status,
            evidence_refs=flat,
            actions=actions,
            verifier_checks=verifier_checks,
        )

    # ---------- 记忆合并（BRS 波后按序 / DRS 每步） ----------

    async def _merge_outcomes(
        self, tasks: list[PracticeTask], outcomes: list[PracticeOutcome], app_context: dict
    ) -> tuple[list[dict], int]:
        reflector = app_context["reflector"]
        by_id = {t.id: t for t in tasks}
        committed: list = []
        for outcome in outcomes:  # 顺序合并
            task = by_id.get(outcome.task_id)
            if task is None:
                continue
            if outcome.status == "error":
                proposals = []
            else:
                proposals = reflector(task, outcome)
                if inspect.isawaitable(proposals):
                    proposals = await proposals
            for p in proposals:
                candidate = await self._writer.propose(
                    kind=p["kind"],
                    content=p["content"],
                    applicability=p["applicability"],
                    evidence_refs=p.get("evidence_refs", []),
                    source_refs=[outcome.task_id],
                )
                report = await self._writer.validate(candidate)
                if not report.valid:
                    continue
                committed.append(await self._writer.commit(candidate))

        hints: list[dict] = []
        if committed:
            # I4: 冲突检测必须覆盖库内已有记录（新候选 vs 已发布规则），
            # 不止波内互查——去重后合并检测集
            namespaces = {r.namespace for r in committed}
            existing: list = []
            for ns in namespaces:
                existing.extend(await self._store.query(ns, {}))
            known_ids = {r.id for r in committed}
            detection_set = committed + [r for r in existing if r.id not in known_ids]
            conflicts = await self._consolidator.detect_conflicts(detection_set)
            for conflict in conflicts:
                resolution = await self._consolidator.resolve(conflict)
                if resolution.action == "dispute":
                    hints.append(
                        {
                            "code": f"CONTRAST:{conflict.subject}",
                            "family": "contrast",
                            "subject": conflict.subject,
                            **resolution.contrast_task_hint,
                        }
                    )
        return hints, len(committed)

    # ---------- DRS ----------

    async def _drs(
        self,
        target: TaskSpec,
        verifier: Verifier,
        app_context: dict,
        meter: BudgetMeter,
        report: ExplorationReport,
        hints: list[dict],
    ) -> None:
        prev_task = {
            "id": target.id,
            "family": TaskFamily.TARGET_ATTEMPT.value,
            "title": "目标直接尝试",
            "objective": target.goal,
        }
        outcome = PracticeOutcome(
            task_id=target.id,
            family=TaskFamily.TARGET_ATTEMPT.value,
            status="failed",
        )
        cursor = _CallCursor(app_context.get("model_counter"))
        for _step in range(app_context.get("policy_max_deep", 3)):
            view = await self._assembler.assemble(
                mode="deep",
                memory_store=self._store,
                gap_ledger=self._gaps,
                recent_outcomes=[{"task_id": outcome.task_id, "status": outcome.status}],
            )
            decision = await self._curriculum.should_stop(view, meter.status)
            if decision.action == "stop":
                break

            produced = await self._curriculum.refine_after_result(
                prev_task, outcome.model_dump(), _diagnose(outcome), view
            )
            if isinstance(produced, CurriculumDecision):
                if produced.action == "stop":
                    break
                continue

            new_outcome = await self._run_practice(produced, verifier, app_context)
            report.tasks_executed += 1
            report.tasks_passed += 1 if new_outcome.status == "passed" else 0
            report.gaps_opened += await self._record_gaps([produced], [new_outcome])

            # 先整合记忆，再出下一题（DRS 的关键顺序）
            _, generated = await self._merge_outcomes([produced], [new_outcome], app_context)
            report.candidates_generated += generated

            meter.consume(actions=1, tokens=cursor.take())
            prev_task = {
                "id": produced.id,
                "family": produced.family.value,
                "title": produced.title,
                "objective": produced.objective,
            }
            outcome = new_outcome

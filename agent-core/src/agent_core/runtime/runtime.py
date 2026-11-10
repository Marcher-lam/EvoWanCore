"""AgentRuntime — Run 生命周期、主循环、取消与安全恢复（spec §5.3）。

主循环：propose → authorize → dispatch → observe/evidence → event → checkpoint。
恢复：已 DISPATCHED 且无终态的动作不重新派发（查检查点），标记
OUTCOME_UNKNOWN（RECONCILIATION，回执丢失语义）。
"""

from __future__ import annotations

import asyncio

from agent_core.action import ActionRequest, ActionStatus, action_transition
from agent_core.capability import CapabilityRegistry
from agent_core.events import RuntimeEvent, new_event
from agent_core.evidence import EvidenceStore, Observation
from agent_core.models import ModelGateway
from agent_core.policy import PolicyEngine
from agent_core.run import IllegalTransition, RunHandle, RunState, run_transition
from agent_core.runtime.driver import (
    AgentSpec,
    ExecutionPolicy,
    ModelLoopPolicy,
    Verifier,  # 兼容再导出
)
from agent_core.runtime.eventlog import EventLog
from agent_core.runtime.executor import CapabilityExecutor
from agent_core.runtime.statestore import StateStore
from agent_core.task import TaskHandle, TaskSpec

__all__ = ["AgentRuntime", "Verifier"]


class AgentRuntime:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        registry: CapabilityRegistry,
        policy_engine: PolicyEngine,
        event_log: EventLog,
        state_store: StateStore,
        evidence_store: EvidenceStore,
        verifier: Verifier,
        granted_scopes: list[str] | None = None,
        skill_runner=None,
        execution_policy: ExecutionPolicy | None = None,
        max_steps: int = 20,
        memory_retriever=None,
        context_assembler=None,
        memory_token_budget: int = 400,
        environment_version: str = "unspecified",
        budget_meter=None,
    ) -> None:
        self._gateway = gateway
        self._registry = registry
        self._policy_engine = policy_engine
        self._log = event_log
        self._store = state_store
        self._evidence = evidence_store
        self._verifier = verifier
        self._granted = list(granted_scopes or [])
        self._executor = CapabilityExecutor(registry=registry, skill_runner=skill_runner)
        self._execution_policy = execution_policy
        self._max_steps = max_steps
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancelled: set[str] = set()
        self._memory_retriever = memory_retriever
        self._context_assembler = context_assembler
        self._memory_token_budget = memory_token_budget
        self._environment_version = environment_version
        self._budget_meter = budget_meter  # 鸭型：consume(**kw) / exhausted: bool

    # ---------- 事件与状态助手 ----------

    async def _emit(
        self, run: RunHandle, event: RuntimeEvent, causation: str | None = None
    ) -> None:
        await self._log.append(
            event.model_copy(
                update={"correlation_id": run.id, "causation_id": causation or event.causation_id}
            )
        )

    async def _save(self, run: RunHandle, old_state: RunState, new_state: RunState) -> RunHandle:
        await self._store.save_run(run)
        await self._emit(
            run,
            new_event(
                "RunStateChanged", "run", run.id,
                state=new_state.value, prev=old_state.value,
            ),
        )
        return run

    def _capability_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": d.id,
                    "description": d.name,
                    "parameters": d.input_schema or {"type": "object"},
                },
            }
            for d in self._registry.discover({})
        ]

    def events(self, run_id: str):
        """M8：公有事件流（外部消费免触 _log）。"""
        return self._log.iter_events(run_id)

    # ---------- 提交与主循环 ----------

    async def _assemble_memory(self, task: TaskSpec) -> str:
        if self._memory_retriever is None:
            return ""
        records = await self._memory_retriever.retrieve(
            scope={"namespaces": ["rules"]},
            task_context={
                "environment_version": self._environment_version,
                "keywords": [task.kind.split(":")[-1], str(task.goal.get("what", ""))],
            },
            limit=8,
        )
        if self._context_assembler is not None:
            return self._context_assembler.assemble(records, self._memory_token_budget)
        return "\n".join(str(r.content) for r in records)

    async def _save_checkpoint(
        self, run: RunHandle, context: dict, extra: dict | None = None
    ) -> None:
        """检查点持久化 context 子集（task spec + pending + history）——resume 重建依据。"""
        state: dict = {
            "task": context["task"].model_dump(),
            "pending": list(context["pending"]),
            "history": list(context["history"]),
        }
        if extra:
            state.update(extra)
        await self._store.save_checkpoint(run.id, state)

    async def submit(self, task: TaskSpec, agent: AgentSpec) -> RunHandle:
        task_handle = TaskHandle(id=f"task_{task.id}", spec_id=task.id, assigned_agent=agent.id)
        await self._store.save_task(task_handle)
        run = RunHandle(task_id=task_handle.id, agent_id=agent.id)
        await self._emit(run, new_event("TaskCreated", "task", task_handle.id, kind=task.kind))
        await self._emit(run, new_event("RunCreated", "run", run.id, agent=agent.id))

        old = run.state
        run = run_transition(run, RunState.QUEUED)
        await self._save(run, old, run.state)
        old = run.state
        run = run_transition(run, RunState.RUNNING)
        await self._save(run, old, run.state)

        context: dict = {
            "task": task,
            "capability_schemas": self._capability_schemas(),
            "history": [],
            "memory": await self._assemble_memory(task),
            "pending": [],
        }
        await self._save_checkpoint(run, context)
        await self._execution_loop(run, agent, context)
        return await self._finish_run(run)

    async def _execution_loop(self, run: RunHandle, agent: AgentSpec, context: dict) -> None:
        policy = self._execution_policy or ModelLoopPolicy(gateway=self._gateway, agent=agent)

        for _step in range(self._max_steps):
            if run.id in self._cancelled:
                break
            proposals = await policy.propose_next_actions(run, context)
            if not proposals:
                break
            stop = False
            for action in proposals:
                outcome = await self._execute_action(run, action, context)
                if outcome == "cancelled":
                    stop = True
                    break
                if self._budget_meter is not None:
                    self._budget_meter.consume(actions=1)
                    if self._budget_meter.exhausted:
                        stop = True
                        break
            if stop or policy.should_yield(run, context):
                break

    async def _finish_run(self, run: RunHandle) -> RunHandle:
        if self._budget_meter is not None and self._budget_meter.exhausted:
            return await self._fail_budget_exhausted(run)
        return await self.verify_and_finish(run.id)

    async def _fail_budget_exhausted(self, run: RunHandle) -> RunHandle:
        """预算耗尽 → FAILED(reason=budget)，跳过验证（取消优先守卫与 M7 一致）。"""
        run = await self._store.load_run(run.id) or run
        if run.state in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}:
            return run
        if run.state is RunState.CANCELLING:
            old = run.state
            run = run_transition(run, RunState.CANCELLED)
            return await self._save(run, old, run.state)
        old = run.state
        run = run_transition(run, RunState.FAILED)
        await self._save(run, old, run.state)
        await self._emit(
            run, new_event("RunBudgetExhausted", "run", run.id, reason="budget")
        )
        return run

    async def _execute_action(self, run: RunHandle, action: ActionRequest, context: dict) -> str:
        proposed = new_event(
            "ActionProposed", "action", action.id,
            capability_id=action.capability_id, arguments=action.arguments,
        )
        await self._emit(run, proposed)

        try:
            capability = self._registry.resolve(action.capability_id)
        except KeyError:
            capability = None
        decision = self._policy_engine.authorize(
            action, {"capability": capability, "granted_scopes": self._granted}
        )
        if not decision.allowed:
            await self._emit(
                run,
                new_event("ActionRejected", "action", action.id, reason=decision.reason),
                causation=proposed.id,
            )
            context["history"].append(
                {"capability_id": action.capability_id, "arguments": action.arguments,
                 "status": "rejected"}
            )
            return "rejected"

        status = action_transition(ActionStatus.PROPOSED, ActionStatus.VALIDATED)
        status = action_transition(status, ActionStatus.AUTHORIZED)
        await self._emit(
            run, new_event("ActionAuthorized", "action", action.id), causation=proposed.id
        )
        status = action_transition(status, ActionStatus.DISPATCHED)
        await self._emit(
            run,
            new_event("ActionDispatched", "action", action.id, capability_id=action.capability_id),
            causation=proposed.id,
        )
        # 派发即检查点：崩溃后恢复时此动作不得重复派发
        context["pending"].append(action.id)
        await self._save_checkpoint(run, context, {"step_pending": action.id})

        result = await self._dispatch_with_cancel(run, action)
        if result is None:  # 取消：回执丢失语义
            context["pending"].remove(action.id)
            await self._save_checkpoint(run, context)
            return "cancelled"

        context["pending"].remove(action.id)
        if result.status is ActionStatus.SUCCEEDED:
            obs_id = await self._evidence.write_observation(
                Observation(
                    environment_version="runtime-v1",
                    state_summary={
                        "action_id": action.id,
                        "capability_id": action.capability_id,
                        "status": result.status.value,
                        "output": result.output,
                    },
                )
            )
            ev_id = await self._evidence.write_evidence(
                claim={"action": action.capability_id, "status": "succeeded",
                       "output_keys": sorted(result.output.keys())},
                observation_refs=[obs_id],
                derivation={"mode": action.mode.value},
            )
            result.evidence_refs.append(ev_id)
            await self._emit(
                run,
                new_event("ActionSucceeded", "action", action.id,
                          evidence_refs=[ev_id], output=result.output),
                causation=proposed.id,
            )
        else:
            await self._emit(
                run,
                new_event("ActionFailed", "action", action.id, error=result.error),
                causation=proposed.id,
            )
        context["history"].append(
            {"capability_id": action.capability_id, "arguments": action.arguments,
             "status": result.status.value}
        )
        await self._save_checkpoint(run, context)
        return "done"

    async def _dispatch_with_cancel(self, run: RunHandle, action: ActionRequest):
        cancel_event = self._cancel_events.setdefault(run.id, asyncio.Event())
        dispatch_task = asyncio.create_task(self._executor.dispatch(action))
        cancel_wait = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait(
            {dispatch_task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        if dispatch_task in done:
            cancel_wait.cancel()
            return dispatch_task.result()
        # 取消先到：回执未回 → OUTCOME_UNKNOWN；后台任务结果到达后被丢弃
        dispatch_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        await self._emit(
            run,
            new_event("ActionOutcomeUnknown", "action", action.id,
                      action_id=action.id, reason="cancelled mid-dispatch: receipt lost"),
        )
        return None

    # ---------- 取消 / 恢复 / 验证 ----------

    async def cancel(self, run_id: str) -> None:
        run = await self._store.load_run(run_id)
        if run is None or run.state in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}:
            return
        self._cancelled.add(run_id)
        self._cancel_events.setdefault(run_id, asyncio.Event()).set()
        if run.state is RunState.RUNNING:
            old = run.state
            run = run_transition(run, RunState.CANCELLING)
            await self._save(run, old, run.state)
            # CANCELLING→CANCELLED 由主循环退出时完成；这里先持久化 CANCELLING
            await asyncio.sleep(0)
        else:
            old = run.state
            run = run_transition(run, RunState.CANCELLED)
            await self._save(run, old, run.state)

    async def _load(self, run_id: str) -> RunHandle:
        return await self._store.load_run(run_id)

    async def resume(self, run_id: str) -> RunHandle:
        run = await self._store.load_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} 不存在")
        if run.state in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}:
            raise IllegalTransition(f"终态 run 不可恢复: {run.state}")

        checkpoint = await self._store.load_latest_checkpoint(run_id) or {}
        for action_id in checkpoint.get("pending", []):
            await self._emit(
                run,
                new_event(
                    "ActionOutcomeUnknown", "action", action_id,
                    action_id=action_id,
                    reason="crash recovery: receipt lost (RECONCILIATION)",
                ),
            )
        if run.state is RunState.SUSPENDED:
            old = run.state
            run = run_transition(run, RunState.RUNNING)
            await self._save(run, old, run.state)

        # T6：重建 context（task 子集 + history 来自检查点）继续主循环，而非仅对账收口
        task_payload = checkpoint.get("task")
        if task_payload:
            task = TaskSpec(**task_payload)
        else:
            handle = await self._store.load_task(run.task_id)
            task = TaskSpec(
                id=handle.spec_id if handle else run.task_id,
                kind="recovered",
                goal={},
                input_schema={},
                constraints={},
                acceptance_criteria=[{"check": "recovered"}],
            )
        agent = AgentSpec(id=run.agent_id, role="worker", model_id="recovered")
        context: dict = {
            "task": task,
            "capability_schemas": self._capability_schemas(),
            "history": list(checkpoint.get("history", [])),
            "memory": await self._assemble_memory(task),
            "pending": [],
        }
        await self._save_checkpoint(run, context, {"reconciled": True})
        await self._execution_loop(run, agent, context)
        return await self._finish_run(run)

    async def verify_and_finish(self, run_id: str) -> RunHandle:
        run = await self._store.load_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} 不存在")
        if run.state in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}:
            return run
        if run.state is RunState.CANCELLING:
            old = run.state
            run = run_transition(run, RunState.CANCELLED)
            return await self._save(run, old, run.state)

        task = await self._store.load_task(run.task_id)
        evidence_refs: list[str] = []
        async for e in self._log.iter_events(run_id):
            if e.type == "ActionSucceeded":
                evidence_refs.extend(e.payload.get("evidence_refs", []))
        result = await self._verifier.verify_task(task, evidence_refs)
        # M7: 验证期间可能收到取消——终态迁移前重读最新状态，取消优先
        run = await self._store.load_run(run_id) or run
        if run.state is RunState.CANCELLING:
            old = run.state
            run = run_transition(run, RunState.CANCELLED)
            return await self._save(run, old, run.state)
        if run.state is RunState.CANCELLED:
            return run
        old = run.state
        target = RunState.SUCCEEDED if result.passed else RunState.FAILED
        run = run_transition(run, target)
        await self._save(run, old, run.state)
        await self._emit(
            run,
            new_event("RunVerified", "run", run.id, passed=result.passed, checks=result.checks),
        )
        return run

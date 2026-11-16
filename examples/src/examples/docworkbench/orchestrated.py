"""docworkbench 编排层（V16-T1）——分派/并行/重派/审计。

北极星保护：执行单元 = `DocumentAgentApp.solve`（vision 约束/T23 原始文档
语义都在 app 层）——本层只管"任务→solver"的多 agent 协作，不改 app 状态机。

与平台 Orchestrator 的分工（docstring 契约）：Orchestrator 绑定裸
AgentRuntime.submit；docworkbench 任务需要 app 层包装（corpus_task →
TaskSpec + vision + derived 清理），故选人复用平台
CapabilityCompositionPolicy、执行走 app.solve——编排语义在分派层实现。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agent_core.orchestration import CapabilityCompositionPolicy


@dataclass
class FailedRun:
    """编排层失败占位（RunHandle 契约对齐：id + state.value）。"""

    task_id: str

    @property
    def id(self) -> str:
        return self.task_id

    @property
    def state(self):
        class _S:
            value = "failed"

        return _S()


def wb_agents() -> list:
    """四维度 solver + audit（generator DIMS 全覆盖——无静默兜底）。"""
    from agent_core.runtime.driver import AgentSpec

    dims = ["wb:extract", "wb:export", "wb:convert", "wb:validate"]
    agents = [
        AgentSpec(id=f"solver-{d.split(':')[1]}", role=d.split(":")[1],
                  capabilities=[d], model_id="wb")
        for d in dims
    ]
    agents.append(AgentSpec(id="auditor", role="auditor",
                            capabilities=["wb:audit"], model_id="wb"))
    return agents


_SOLVER_FALLBACK = {
    "solver-extract": "solver-validate",  # 重派换视角：验证型 solver 复核
    "solver-export": "solver-convert",
    "solver-convert": "solver-export",
    "solver-validate": "solver-extract",
}


class OrchestratedWorkbench:
    """多 solver 协作层：能力选人 → 并行批 → 失败重派 → 审计诊断。"""

    def __init__(self, app: Any, *, max_retries: int = 1) -> None:
        self._app = app
        self._max_retries = max_retries
        self._policy = CapabilityCompositionPolicy()
        self._agents = wb_agents()

    def _select_solver(self, task: dict) -> str:
        """平台真选人：dimension → wb:{dim} → 对应 solver（无命中 raise）。

        probe 用 SimpleNamespace（policy 只读 .kind；TaskSpec(id="") 会
        过 pydantic 校验——实测并行批全炸 ValidationError）。
        """
        probe = SimpleNamespace(kind=f"wb:{task.get('dimension', '')}")
        selected = self._policy.select_participants(probe, self._agents)
        if not selected:
            raise ValueError(f"无 solver 覆盖维度: {task.get('dimension')}")
        return selected[0]

    async def solve_one(self, task: dict) -> dict:
        """单任务：选人 → solve → 失败重派（≤max_retries）→ 审计诊断。"""
        solver = self._select_solver(task)
        run = None
        redispatched = 0
        for _attempt in range(1 + self._max_retries):
            run = await self._app.solve(task)
            if run.state.value == "succeeded":
                break
            redispatched += 1  # 重派第二 solver（T23 保证从原始文档重跑）
        result = {
            "task_id": task["id"], "solver": solver,
            "state": run.state.value if run else "failed",
            "redispatched": redispatched,
        }
        if result["state"] != "succeeded":
            result["diagnosis"] = await self._audit(task, run)
        return result

    async def solve_batch(self, tasks: list[dict]) -> list[dict]:
        """并行分派 + 异常隔离（对齐平台 Orchestrator：单任务炸不弃兄弟）。

        批内 doc_id 必须唯一（app.derived 共享 dict——串行重试安全，
        并行共享 doc 会串味，入口断言显式拒绝）。
        """
        doc_ids = [t.get("doc_id") for t in tasks if t.get("doc_id")]
        if len(doc_ids) != len(set(doc_ids)):
            raise ValueError("批内 doc_id 重复（derived 并发串味风险）")
        outcomes = await asyncio.gather(
            *[self.solve_one(t) for t in tasks], return_exceptions=True,
        )
        results = []
        for task, outcome in zip(tasks, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                results.append({
                    "task_id": task["id"],
                    "solver": "error", "state": "failed",
                    "redispatched": 0,
                    "diagnosis": {"stage": "exception",
                                  "last_event": type(outcome).__name__,
                                  "dimension": task.get("dimension")},
                })
            else:
                results.append(outcome)
        return results

    async def _audit(self, task: dict, run: Any) -> dict:
        """auditor 角色：读失败 run 事件流 → 失败阶段分类。

        失败阶段语义：最后事件类型映射（convert 卡死 / extract 读错 /
        验证拒绝）——供学习反射器（B 闭环）消费的失败画像。
        """
        counts: dict[str, int] = {}
        last_type = ""
        try:
            async for e in self._app.events(run.id):
                last_type = e.type
                counts[e.type] = counts.get(e.type, 0) + 1
            # 聚合全流分类（终态前恒 RunVerified——只看 last 会全归 verify）
            if counts.get("ActionFailed", 0) > 0:
                stage = "action"
            elif counts.get("ActionRejected", 0) > 0:
                stage = "rejected"
            elif last_type:
                stage = "loop-exit"
            else:
                stage = "no-events"
        except Exception:  # noqa: BLE001 — 诊断失败不掩盖原失败
            stage = "unknown"
        return {"stage": stage, "last_event": last_type,
                "dimension": task.get("dimension")}

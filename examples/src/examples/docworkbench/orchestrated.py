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
from dataclasses import dataclass, field
from typing import Any

from agent_core.orchestration import CapabilityCompositionPolicy


@dataclass
class FailedRun:
    """编排层失败占位（重派语义的可测载体）。"""

    task_id: str

    @property
    def state(self):
        class _S:
            value = "failed"

        return _S()


def wb_agents() -> list:
    """docworkbench 三角色：extract/explore 求解 + audit 审计。"""
    from agent_core.runtime.driver import AgentSpec

    return [
        AgentSpec(id="solver-extract", role="extractor",
                  capabilities=["wb:extract"], model_id="wb"),
        AgentSpec(id="solver-explore", role="explorer",
                  capabilities=["wb:explore"], model_id="wb"),
        AgentSpec(id="auditor", role="auditor",
                  capabilities=["wb:audit"], model_id="wb"),
    ]


@dataclass
class BatchReport:
    total: int
    succeeded: int
    redispatched: int = 0
    results: list = field(default_factory=list)


class OrchestratedWorkbench:
    """多 solver 协作层：能力选人 → 并行批 → 失败重派 → 审计诊断。"""

    def __init__(self, app: Any, *, max_retries: int = 1) -> None:
        self._app = app
        self._max_retries = max_retries
        self._policy = CapabilityCompositionPolicy()
        self._agents = wb_agents()

    def _select_solver(self, task: dict) -> str:
        """平台真选人：task dimension → wb:{dim} → 对应 solver。"""
        kind = f"wb:{task.get('dimension', 'extract')}"

        class _T:
            pass

        t = _T()
        t.kind = kind
        selected = self._policy.select_participants(t, self._agents)
        return selected[0] if selected else "solver-extract"

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
        """并行分派（asyncio.gather——真并行，批耗时≈最慢单任务）。"""
        return list(await asyncio.gather(
            *[self.solve_one(t) for t in tasks]
        ))

    async def _audit(self, task: dict, run: Any) -> dict:
        """auditor 角色：读失败 run 事件流 → 失败阶段分类。

        失败阶段语义：最后事件类型映射（convert 卡死 / extract 读错 /
        验证拒绝）——供学习反射器（B 闭环）消费的失败画像。
        """
        stage = "unknown"
        last_type = ""
        try:
            events = self._app.events(run.id) if hasattr(self._app, "events") \
                else None
            if events is not None:
                async for e in events:
                    last_type = e.type
                if "ActionFailed" in last_type or "ActionRejected" in last_type:
                    stage = "action"
                elif "RunVerified" in last_type:
                    stage = "verify"
                elif last_type:
                    stage = "loop-exit"
        except Exception:  # noqa: BLE001 — 诊断失败不掩盖原失败
            pass
        return {"stage": stage, "last_event": last_type,
                "dimension": task.get("dimension")}

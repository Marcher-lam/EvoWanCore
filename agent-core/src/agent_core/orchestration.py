"""多 Agent 编排——CompositionPolicy 实现 + Orchestrator（V5 T4-T5）。

实现 reserved.py 的 CompositionPolicy Protocol：
- CapabilityCompositionPolicy：按 Agent 声明的 capabilities 与任务 kind 匹配
- Orchestrator：选择参与者 → asyncio.gather 并行 submit → 终态汇总
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_core.runtime.runtime import AgentRuntime
from agent_core.task import TaskSpec


class CapabilityCompositionPolicy:
    """按能力匹配选参与者；全部到达终态则终止。"""

    def select_participants(self, task: Any, agents: list[Any]) -> list[str]:
        kind = str(getattr(task, "kind", ""))
        return [
            a.id for a in agents
            if kind in getattr(a, "capabilities", [])
        ]

    def decide_termination(self, task: Any, context: dict) -> str:
        states = context.get("participants", {})
        if not states:
            return "terminate"
        terminal = {"succeeded", "failed", "cancelled"}
        return "terminate" if all(s in terminal for s in states.values()) else "continue"


class Orchestrator:
    """多 Agent 并行执行：策略选人 → gather submit → 汇总终态。"""

    def __init__(
        self,
        policy: CapabilityCompositionPolicy,
        runtimes: dict[str, AgentRuntime],
    ) -> None:
        self._policy = policy
        self._runtimes = runtimes

    async def orchestrate(
        self, task: TaskSpec, agents: list[Any]
    ) -> dict[str, str]:
        """返回 {agent_id: final_state}。"""
        participants = self._policy.select_participants(task, agents)
        if not participants:
            return {}

        agent_map = {a.id: a for a in agents}

        async def _run_one(agent_id: str) -> tuple[str, str]:
            runtime = self._runtimes[agent_id]
            spec = task.model_copy(
                update={"id": f"{task.id}:{agent_id}"}
            )
            run = await runtime.submit(spec, agent_map[agent_id])
            return agent_id, run.state.value

        results = await asyncio.gather(*[_run_one(a) for a in participants])
        return dict(results)

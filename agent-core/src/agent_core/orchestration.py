"""多 Agent 编排——CompositionPolicy 实现 + Orchestrator（V5 T4-T5，V6 T3-T4）。

实现 reserved.py 的 CompositionPolicy Protocol：
- CapabilityCompositionPolicy：按 Agent 声明的 capabilities 与任务 kind 匹配
- Orchestrator：选择参与者 → asyncio.gather 并行 submit → 终态汇总
V6 追加（LLM 驱动调度，async-native——reserved 同步 Protocol 冻结不动）：
- LLMCompositionPolicy：planner（LLM/任意可调用）动态选人 + 终止判定；
  输出过白名单校验（幻觉 id 丢弃、非法输出 fail-safe 终止）
- ReplanningOrchestrator：多轮"选人→并行执行→终止判定→重规划"循环
- StaticPolicyAdapter：把同步静态策略适配进重规划循环
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
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
    """多 Agent 并行执行：策略选人 → gather submit → 汇总终态。

    一次性并行模式：所有参与者被提交并各自运行到终态，终止是结构性的
    （gather 全返回）；policy.decide_termination 在汇总后调用并如实回传
    decision——供调用方在迭代编排循环中复用同一策略。
    """

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
        """返回 {agent_id: final_state}；单个 agent 异常 → "failed"（不弃兄弟结果）。"""
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

        results = await asyncio.gather(
            *[_run_one(a) for a in participants], return_exceptions=True
        )
        states: dict[str, str] = {}
        for agent_id, outcome in zip(participants, results, strict=True):
            if isinstance(outcome, BaseException):
                states[agent_id] = "failed"  # 异常隔离：不中止兄弟任务
            else:
                states[agent_id] = outcome[1]
        # 策略终止判定如实执行（一次性模式下必为 terminate；保留接线供迭代复用）
        decision = self._policy.decide_termination(
            task, {"participants": states}
        )
        assert decision == "terminate", "一次性并行模式要求全部终态后终止"
        return states


# ---------- V6-T3/T4: LLM 驱动动态调度 ----------

Planner = Callable[[str], Any]  # 同步 str→str 或 async str→str


async def _call_planner(planner: Planner, prompt: str) -> str:
    raw = planner(prompt)
    if inspect.isawaitable(raw):
        raw = await raw
    return str(raw)


def _parse_id_list(raw: str) -> list[str]:
    """宽容解析 JSON 数组；解析失败 → []（fail-safe：不派任何人）。"""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [x for x in parsed if isinstance(x, str)]


class LLMCompositionPolicy:
    """LLM 动态调度：planner 可为同步/异步可调用（FakeModel 或网关桥皆可）。

    红线：planner 输出必须过白名单校验——
    - 选人：只保留已知 agent id（幻觉/注入 id 丢弃；全无效 → 空）
    - 终止：只认 "continue"；其他任何输出 → "terminate"（fail-safe 停止）
    """

    def __init__(self, planner: Planner) -> None:
        self._planner = planner

    async def select_participants(
        self, task: Any, agents: list[Any], context: dict | None = None
    ) -> list[str]:
        known = [a.id for a in agents]
        prompt = (
            f"任务 kind={getattr(task, 'kind', '')} goal={getattr(task, 'goal', {})}\n"
            f"候选 agents: "
            f"{[(a.id, getattr(a, 'capabilities', [])) for a in agents]}\n"
            f"已执行轮次摘要: {json.dumps(context or {}, ensure_ascii=False, default=str)}\n"
            "只输出 JSON 数组（要派发的 agent id），不要其他文字。"
        )
        raw = await _call_planner(self._planner, prompt)
        requested = _parse_id_list(raw)
        return [i for i in requested if i in known]  # 白名单：幻觉 id 丢弃

    async def decide_termination(self, task: Any, context: dict) -> str:
        prompt = (
            f"任务 kind={getattr(task, 'kind', '')}\n"
            f"参与者终态: {json.dumps(context.get('participants', {}), default=str)}\n"
            '输出 "continue"（继续下一轮重规划）或 "terminate"（全部完成/放弃）。'
        )
        raw = await _call_planner(self._planner, prompt)
        return "continue" if "continue" in raw.strip().lower() else "terminate"


class StaticPolicyAdapter:
    """把同步 CompositionPolicy（如 CapabilityCompositionPolicy）适配为 async-native。"""

    def __init__(self, policy: Any) -> None:
        self._policy = policy

    async def select_participants(
        self, task: Any, agents: list[Any], context: dict | None = None
    ) -> list[str]:
        return self._policy.select_participants(task, agents)

    async def decide_termination(self, task: Any, context: dict) -> str:
        return self._policy.decide_termination(task, context)


class ReplanningOrchestrator:
    """多轮重规划循环：选人（带 context）→ 并行执行 → 终止判定 → 下一轮。

    语义：已成功者不重跑；单 agent 异常隔离为 failed；无活可派即收口；
    max_rounds 硬上界防策略死循环。
    """

    def __init__(
        self,
        policy: Any,  # LLMCompositionPolicy | StaticPolicyAdapter
        runtimes: dict[str, Any],
        max_rounds: int = 4,
    ) -> None:
        self._policy = policy
        self._runtimes = runtimes
        self._max_rounds = max_rounds

    async def orchestrate(
        self, task: TaskSpec, agents: list[Any]
    ) -> dict[str, str]:
        agent_map = {a.id: a for a in agents}
        states: dict[str, str] = {}
        context: dict = {"participants": states, "rounds": []}

        for _round in range(self._max_rounds):
            selected = await self._policy.select_participants(task, agents, context)
            todo = [
                aid for aid in selected
                if aid in self._runtimes and states.get(aid) != "succeeded"
            ]
            if not todo:
                break  # 无人可派（含：策略只提名已成功者）→ 收口

            async def _run_one(agent_id: str) -> tuple[str, str]:
                runtime = self._runtimes[agent_id]
                spec = task.model_copy(update={"id": f"{task.id}:{agent_id}"})
                run = await runtime.submit(spec, agent_map[agent_id])
                return agent_id, run.state.value

            results = await asyncio.gather(
                *[_run_one(a) for a in todo], return_exceptions=True
            )
            round_record: dict[str, str] = {}
            for agent_id, outcome in zip(todo, results, strict=True):
                state = "failed" if isinstance(outcome, BaseException) else outcome[1]
                states[agent_id] = state
                round_record[agent_id] = state
            context["rounds"].append(round_record)

            if await self._policy.decide_termination(task, context) != "continue":
                break
        return states

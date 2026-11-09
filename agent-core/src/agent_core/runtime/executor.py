"""CapabilityExecutor — 动作执行网关（spec §5.3）。

TOOL 进程内调用绑定实现；SKILL 必须经注入的沙箱执行器（T16），
未注入即拒绝；SIMULATE 只返回干跑描述。
"""

from __future__ import annotations

import inspect
from typing import Protocol

from agent_core.action import ActionMode, ActionRequest, ActionResult, ActionStatus
from agent_core.capability import CapabilityRegistry, CapabilityType


class SkillRunner(Protocol):
    async def run(self, request: ActionRequest) -> ActionResult: ...


class CapabilityExecutor:
    def __init__(self, *, registry: CapabilityRegistry, skill_runner: SkillRunner | None) -> None:
        self._registry = registry
        self._skill_runner = skill_runner

    async def dispatch(self, action: ActionRequest) -> ActionResult:
        try:
            descriptor = self._registry.resolve(action.capability_id)
        except KeyError:
            return ActionResult(
                action_id=action.id,
                status=ActionStatus.FAILED,
                error={"reason": f"能力未注册: {action.capability_id}"},
            )

        if action.mode is ActionMode.SIMULATE:
            return ActionResult(
                action_id=action.id,
                status=ActionStatus.SUCCEEDED,
                output={"simulated": True, "capability": action.capability_id,
                        "arguments": action.arguments},
            )

        if descriptor.type is CapabilityType.SKILL:
            if self._skill_runner is None:
                return ActionResult(
                    action_id=action.id,
                    status=ActionStatus.FAILED,
                    error={"reason": "skill 执行器未配置：默认拒绝执行候选代码"},
                )
            return await self._skill_runner.run(action)

        handler = self._registry.handler_for(action.capability_id)
        if handler is None:
            return ActionResult(
                action_id=action.id,
                status=ActionStatus.FAILED,
                error={"reason": f"能力 {action.capability_id} 无绑定实现"},
            )
        try:
            out = handler(action.arguments)
            if inspect.isawaitable(out):
                out = await out
            return ActionResult(
                action_id=action.id,
                status=ActionStatus.SUCCEEDED,
                output=out if isinstance(out, dict) else {"value": out},
            )
        except Exception as e:  # noqa: BLE001 — 执行器必须把失败转为结果而非炸掉循环
            return ActionResult(
                action_id=action.id,
                status=ActionStatus.FAILED,
                error={"reason": f"{type(e).__name__}: {e}"},
            )

"""PolicyEngine — 权限/预算/审批门控（spec §5.3）。

策略决策发生在每次有副作用动作执行前，不是只在任务开始检查一次。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from agent_core.action import ActionRequest


class Decision(BaseModel):
    allowed: bool
    reason: str
    requires_approval: bool = False


class PolicyEngine(Protocol):
    def authorize(self, action: ActionRequest, context: dict) -> Decision: ...

    def enforce_budget(self, run_id: str, usage: dict) -> Decision: ...


class DefaultPolicyEngine:
    """规则式默认实现：
    - 副作用不可逆 ⇒ 需要审批
    - 能力声明的 auth_scope 未被授予 ⇒ 拒绝
    - 预算任一维度超限 ⇒ 拒绝
    """

    def __init__(self, budget_limits: dict[str, float] | None = None) -> None:
        self._budget_limits = budget_limits or {}

    def authorize(self, action: ActionRequest, context: dict) -> Decision:
        capability = context.get("capability")
        if capability is None:
            return Decision(
                allowed=False, reason=f"能力 {action.capability_id} 未注册，无描述可鉴权"
            )

        granted: set[str] = set(context.get("granted_scopes", []))
        missing = [s for s in capability.auth_scope if s not in granted]
        if missing:
            return Decision(
                allowed=False, reason=f"缺少授权范围: {', '.join(missing)}"
            )

        if capability.side_effect_level == "irreversible":
            return Decision(
                allowed=True, reason="不可逆动作，需审批后执行", requires_approval=True
            )
        return Decision(allowed=True, reason="ok")

    def enforce_budget(self, run_id: str, usage: dict) -> Decision:
        for dim, limit in self._budget_limits.items():
            spent = float(usage.get(dim, 0))
            if spent > float(limit):
                return Decision(
                    allowed=False, reason=f"预算超限: {dim} {spent} > {limit}"
                )
        return Decision(allowed=True, reason="ok")

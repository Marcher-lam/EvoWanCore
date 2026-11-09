"""ActionRequest / ActionResult 与动作调用状态机（spec §5.3）。

"模型建议调用"与"系统实际执行"是两件事；副作用已发出但回执丢失 →
OUTCOME_UNKNOWN（超时 ≠ 失败，禁止据此自动重试）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ActionMode(StrEnum):
    PREVIEW = "preview"
    EXECUTE = "execute"
    SIMULATE = "simulate"
    DELEGATE = "delegate"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    AUTHORIZED = "authorized"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class IllegalActionTransition(Exception):
    """动作状态机守卫拒绝的转移。"""


_TERMINAL = {
    ActionStatus.REJECTED,
    ActionStatus.SUCCEEDED,
    ActionStatus.FAILED,
    ActionStatus.OUTCOME_UNKNOWN,
}

_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.PROPOSED: frozenset({ActionStatus.VALIDATED, ActionStatus.REJECTED}),
    ActionStatus.VALIDATED: frozenset({ActionStatus.AUTHORIZED, ActionStatus.REJECTED}),
    ActionStatus.AUTHORIZED: frozenset({ActionStatus.DISPATCHED, ActionStatus.REJECTED}),
    ActionStatus.DISPATCHED: frozenset(
        {ActionStatus.SUCCEEDED, ActionStatus.FAILED, ActionStatus.OUTCOME_UNKNOWN}
    ),
}


class ActionRequest(BaseModel):
    id: str
    task_id: str
    run_id: str
    actor_id: str
    capability_id: str
    arguments: dict = {}
    mode: ActionMode = ActionMode.EXECUTE
    justification: dict = {}


class ActionResult(BaseModel):
    action_id: str
    status: ActionStatus
    output: dict = {}
    artifacts: list[str] = []
    evidence_refs: list[str] = []
    error: dict | None = None


def action_transition(
    status: ActionStatus,
    target: ActionStatus,
    *,
    timed_out: bool = False,
) -> ActionStatus:
    if status in _TERMINAL:
        raise IllegalActionTransition(f"终态 {status} 不可再转移（目标 {target}）")
    if target not in _TRANSITIONS[status]:
        raise IllegalActionTransition(f"非法转移: {status} → {target}")
    if target is ActionStatus.OUTCOME_UNKNOWN and not timed_out:
        raise IllegalActionTransition("OUTCOME_UNKNOWN 仅当 timed_out=True（回执丢失语义）")
    return target

"""Run 生命周期状态机（spec §5.3，comunication-3 第 5 轮修订版）。

内核管理通用生命周期；规划等阶段是策略自己的子状态，不进状态机。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RunState(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUSPENDED = "suspended"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class WaitReason(StrEnum):
    INPUT = "input"
    APPROVAL = "approval"
    CHILD_TASK = "child_task"
    MESSAGE = "message"
    TIMER = "timer"
    RESOURCE = "resource"
    RECONCILIATION = "reconciliation"


class IllegalTransition(Exception):
    """状态机守卫拒绝的转移。"""


_TERMINAL = {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}

# spec §5.3 锁定的合法转移表
_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.QUEUED}),
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(
        {RunState.SUSPENDED, RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLING}
    ),
    RunState.SUSPENDED: frozenset({RunState.RUNNING, RunState.CANCELLING}),
    RunState.CANCELLING: frozenset({RunState.CANCELLED}),
}


class RunHandle(BaseModel):
    id: str = Field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    task_id: str
    agent_id: str
    state: RunState = RunState.CREATED
    wait_reason: WaitReason | None = None
    rerun_of: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def run_transition(
    run: RunHandle,
    target: RunState,
    wait_reason: WaitReason | None = None,
) -> RunHandle:
    """按转移表推进 Run；非法转移抛 IllegalTransition。返回更新后的句柄（不改原对象）。"""
    if run.state in _TERMINAL:
        raise IllegalTransition(f"终态 {run.state} 不可再转移（目标 {target}）")
    if target not in _TRANSITIONS[run.state]:
        raise IllegalTransition(f"非法转移: {run.state} → {target}")
    if target is RunState.SUSPENDED and wait_reason is None:
        raise IllegalTransition("SUSPENDED 必须携带 wait_reason（结构化等待原因）")
    return run.model_copy(
        update={
            "state": target,
            "wait_reason": wait_reason if target is RunState.SUSPENDED else None,
            "updated_at": datetime.now(UTC),
        }
    )


def rerun(run: RunHandle) -> RunHandle:
    """重新执行 = 新建 Run 并关联原 Run；终态不回退。"""
    return RunHandle(
        task_id=run.task_id,
        agent_id=run.agent_id,
        state=RunState.CREATED,
        rerun_of=run.id,
    )

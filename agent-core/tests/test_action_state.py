"""T5 Action 状态机：模型建议 ≠ 系统执行；OUTCOME_UNKNOWN 语义。"""

import pytest
from agent_core.action import (
    ActionMode,
    ActionRequest,
    ActionResult,
    ActionStatus,
    IllegalActionTransition,
    action_transition,
)
from pydantic import ValidationError


def test_request_defaults_and_mode_enum() -> None:
    req = ActionRequest(
        id="act_1",
        task_id="task_1",
        run_id="run_1",
        actor_id="agent_1",
        capability_id="tool_export",
        arguments={"format": "xlsx"},
        mode=ActionMode.EXECUTE,
        justification={"why": "user asked"},
    )
    assert req.mode is ActionMode.EXECUTE
    assert ActionMode.PREVIEW.value == "preview"


def test_happy_dispatch_path() -> None:
    s = ActionStatus.PROPOSED
    s = action_transition(s, ActionStatus.VALIDATED)
    s = action_transition(s, ActionStatus.AUTHORIZED)
    s = action_transition(s, ActionStatus.DISPATCHED)
    s = action_transition(s, ActionStatus.SUCCEEDED)
    assert s is ActionStatus.SUCCEEDED


def test_rejected_is_terminal() -> None:
    s = action_transition(ActionStatus.PROPOSED, ActionStatus.REJECTED)
    with pytest.raises(IllegalActionTransition):
        action_transition(s, ActionStatus.AUTHORIZED)


def test_validate_gate_blocks_dispatch() -> None:
    with pytest.raises(IllegalActionTransition):
        action_transition(ActionStatus.PROPOSED, ActionStatus.DISPATCHED)  # 未校验不得派发


def test_timeout_yields_outcome_unknown_not_failed() -> None:
    s = action_transition(ActionStatus.DISPATCHED, ActionStatus.OUTCOME_UNKNOWN, timed_out=True)
    assert s is ActionStatus.OUTCOME_UNKNOWN  # 回执丢失 ≠ 失败：不自动重试


def test_outcome_unknown_requires_timeout_flag() -> None:
    with pytest.raises(IllegalActionTransition, match="timed_out"):
        action_transition(ActionStatus.DISPATCHED, ActionStatus.OUTCOME_UNKNOWN)


def test_succeeded_and_failed_are_terminal() -> None:
    for terminal in (ActionStatus.SUCCEEDED, ActionStatus.FAILED, ActionStatus.OUTCOME_UNKNOWN):
        with pytest.raises(IllegalActionTransition):
            action_transition(terminal, ActionStatus.DISPATCHED)


def test_result_defaults() -> None:
    res = ActionResult(
        action_id="act_1", status=ActionStatus.SUCCEEDED, output={"file": "out.xlsx"}
    )
    assert res.artifacts == []
    assert res.evidence_refs == []
    assert res.error is None
    with pytest.raises(ValidationError):
        ActionResult(action_id="act_1", status="banana", output={})

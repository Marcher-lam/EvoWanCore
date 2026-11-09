"""T4 Task/Run 状态机：显式转移表 + 守卫。"""

import pytest
from agent_core.run import (
    IllegalTransition,
    RunHandle,
    RunState,
    WaitReason,
    rerun,
    run_transition,
)
from agent_core.task import TaskHandle, TaskSpec
from pydantic import ValidationError


def _spec() -> TaskSpec:
    return TaskSpec(
        id="spec_1",
        kind="parse",
        goal={"what": "extract fields"},
        input_schema={"doc": "asset_ref"},
        output_schema=None,
        constraints={},
        acceptance_criteria=[{"check": "fields_match"}],
    )


def _run() -> RunHandle:
    return RunHandle(id="run_1", task_id="task_1", agent_id="agent_1", state=RunState.CREATED)


def test_happy_path() -> None:
    run = _run()
    run = run_transition(run, RunState.QUEUED)
    run = run_transition(run, RunState.RUNNING)
    run = run_transition(run, RunState.SUCCEEDED)
    assert run.state is RunState.SUCCEEDED


def test_illegal_transition_raises() -> None:
    run = _run()
    run = run_transition(run, RunState.QUEUED)
    with pytest.raises(IllegalTransition):
        run_transition(run, RunState.SUCCEEDED)  # QUEUED 不能直接成功


def test_suspend_requires_reason() -> None:
    run = _run()
    run = run_transition(run, RunState.QUEUED)
    run = run_transition(run, RunState.RUNNING)
    with pytest.raises(IllegalTransition, match="wait_reason"):
        run_transition(run, RunState.SUSPENDED)
    run = run_transition(run, RunState.SUSPENDED, wait_reason=WaitReason.APPROVAL)
    assert run.wait_reason is WaitReason.APPROVAL
    run = run_transition(run, RunState.RUNNING)
    assert run.wait_reason is None  # 恢复运行清空等待原因


def _to_terminal(terminal: RunState) -> RunHandle:
    """经合法路径到达各终态。"""
    if terminal is RunState.CANCELLED:
        run = _run()
        run = run_transition(run, RunState.QUEUED)
        return run_transition(run, RunState.CANCELLED)
    run = _run()
    run = run_transition(run, RunState.QUEUED)
    run = run_transition(run, RunState.RUNNING)
    return run_transition(run, terminal)


def test_terminal_is_final() -> None:
    for terminal in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED):
        run = _to_terminal(terminal)
        assert run.state is terminal
        for target in (RunState.RUNNING, RunState.QUEUED, RunState.SUSPENDED):
            with pytest.raises(IllegalTransition):
                run_transition(run, target)


def test_running_cannot_jump_to_cancelled() -> None:
    run = _run()
    run = run_transition(run, RunState.QUEUED)
    run = run_transition(run, RunState.RUNNING)
    with pytest.raises(IllegalTransition):
        run_transition(run, RunState.CANCELLED)  # 取消必须经 CANCELLING


def test_cancel_path() -> None:
    run = _run()
    run = run_transition(run, RunState.QUEUED)
    run = run_transition(run, RunState.RUNNING)
    run = run_transition(run, RunState.CANCELLING)
    run = run_transition(run, RunState.CANCELLED)
    assert run.state is RunState.CANCELLED


def test_rerun_creates_new_run_linked() -> None:
    run = _run()
    run = run_transition(run, RunState.QUEUED)
    run = run_transition(run, RunState.RUNNING)
    run = run_transition(run, RunState.FAILED)
    again = rerun(run)
    assert again.id != run.id
    assert again.state is RunState.CREATED
    assert again.rerun_of == run.id
    assert again.task_id == run.task_id


def test_task_spec_requires_acceptance_criteria() -> None:
    with pytest.raises(ValidationError):
        TaskSpec(
            id="bad",
            kind="parse",
            goal={},
            input_schema={},
            output_schema=None,
            constraints={},
            acceptance_criteria=[],  # 空 = 任务无法验收
        )


def test_task_handle_roundtrip() -> None:
    handle = TaskHandle(
        id="task_1", spec_id="spec_1", parent_task_id=None, assigned_agent="agent_1"
    )
    assert TaskHandle.model_validate_json(handle.model_dump_json()) == handle

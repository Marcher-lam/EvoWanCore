"""T10 append-only 事件日志 + SQLite 状态存储（含检查点）。"""

from pathlib import Path

import pytest
from agent_core.events import new_event
from agent_core.run import RunHandle, RunState
from agent_core.runtime.eventlog import SqliteEventLog
from agent_core.runtime.statestore import SqliteStateStore
from agent_core.task import TaskHandle, TaskSpec


@pytest.fixture
def log(tmp_path: Path) -> SqliteEventLog:
    return SqliteEventLog(tmp_path / "log.db")


async def test_eventlog_append_only_order(log: SqliteEventLog) -> None:
    e1 = new_event("TaskCreated", "task", "t1", correlation_id="corr_1", n=1)
    e2 = new_event("RunStateChanged", "run", "r1", correlation_id="corr_1", n=2)
    e3 = new_event("Other", "task", "t2", correlation_id="corr_2")
    await log.append(e1)
    await log.append(e2)
    await log.append(e3)

    got = [e async for e in log.iter_events("corr_1")]
    assert [e.id for e in got] == [e1.id, e2.id]  # 顺序保持
    assert got[0].payload["n"] == 1

    all_events = [e async for e in log.iter_events()]
    assert len(all_events) == 3


async def test_eventlog_has_no_update_delete_api(log: SqliteEventLog) -> None:
    public = {n for n in dir(log) if not n.startswith("_")}
    forbidden = {"update", "delete", "remove", "patch"}
    assert not public & forbidden, "append-only 日志不得暴露更新/删除接口"


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    return SqliteStateStore(tmp_path / "state.db")


def _spec() -> TaskSpec:
    return TaskSpec(
        id="spec_1",
        kind="parse",
        goal={},
        input_schema={},
        constraints={},
        acceptance_criteria=[{"c": 1}],
    )


async def test_statestore_roundtrip(store: SqliteStateStore) -> None:
    spec = _spec()
    task = TaskHandle(id="task_1", spec_id=spec.id, assigned_agent="agent_1")
    await store.save_task(task)
    loaded = await store.load_task("task_1")
    assert loaded == task

    run = RunHandle(id="run_1", task_id="task_1", agent_id="agent_1", state=RunState.RUNNING)
    await store.save_run(run)
    assert await store.load_run("run_1") == run
    assert await store.load_run("missing") is None


async def test_checkpoint_latest_wins(store: SqliteStateStore) -> None:
    await store.save_checkpoint("run_1", {"step": 1, "actions_done": ["a1"]})
    await store.save_checkpoint("run_1", {"step": 2, "actions_done": ["a1", "a2"]})
    latest = await store.load_latest_checkpoint("run_1")
    assert latest["step"] == 2
    assert latest["actions_done"] == ["a1", "a2"]
    assert await store.load_latest_checkpoint("run_none") is None

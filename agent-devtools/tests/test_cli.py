"""T25 CLI：三命令可用。"""

from pathlib import Path

from agent_devtools.cli import cli
from click.testing import CliRunner


def test_help_lists_three_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    out = result.output
    for cmd in ("run-experiment", "show-run", "show-memory"):
        assert cmd in out


def test_show_run_prints_timeline(tmp_path: Path) -> None:
    import asyncio

    from agent_core.events import new_event
    from agent_core.runtime.eventlog import SqliteEventLog

    log = SqliteEventLog(tmp_path / "log.db")

    async def seed():
        e1 = new_event("TaskCreated", "task", "task_1", correlation_id="run_x")
        e2 = new_event(
            "RunStateChanged", "run", "run_x", correlation_id="run_x", state="running"
        )
        await log.append(e1.model_copy(update={"correlation_id": "run_x"}))
        await log.append(e2)

    asyncio.run(seed())
    result = CliRunner().invoke(cli, ["show-run", "run_x", "--log", str(tmp_path / "log.db")])
    assert result.exit_code == 0
    assert "TaskCreated" in result.output
    assert "RunStateChanged" in result.output


def test_show_memory_lists_namespace(tmp_path: Path) -> None:
    import asyncio

    from agent_core.memory.record import MemoryKind, MemoryRecord
    from agent_core.memory.store import NS_RULES, SqliteMemoryStore

    store = SqliteMemoryStore(tmp_path / "mem.db")

    async def seed():
        await store.put(
            MemoryRecord(
                kind=MemoryKind.RULE,
                namespace=NS_RULES,
                content={"statement": "导出前需确认选区"},
                evidence_refs=["evi"],
            )
        )

    asyncio.run(seed())
    result = CliRunner().invoke(
        cli, ["show-memory", "--db", str(tmp_path / "mem.db"), "--ns", "rules"]
    )
    assert result.exit_code == 0
    assert "导出前需确认选区" in result.output


def test_run_experiment_writes_report(tmp_path: Path) -> None:
    import json

    config = {"seeds": [21], "max_actions": 2, "practice_ratio": 0.5, "size": 4, "difficulty": 0.2}
    cfg_path = tmp_path / "exp.json"
    cfg_path.write_text(json.dumps(config))
    out_path = tmp_path / "report.json"
    result = CliRunner().invoke(
        cli,
        ["run-experiment", "--config", str(cfg_path), "--out", str(out_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    data = json.loads(out_path.read_text())
    assert set(data["arms"]) == {"explore", "retry"}
    assert data["seeds"] == [21]

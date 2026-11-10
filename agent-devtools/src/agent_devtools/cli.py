"""devtools CLI（T25）：run-experiment / show-run / show-memory。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click


@click.group()
def cli() -> None:
    """EvoWanCore 开发者工具。"""


@cli.command("run-experiment")
@click.option("--config", required=True, type=click.Path(exists=True), help="实验配置 JSON")
@click.option("--out", default="reports/experiment_report.json", help="报告输出路径")
def run_experiment(config: str, out: str) -> None:
    """同预算对照实验（explore vs retry）。"""
    from agent_explore.curriculum import ExplorationBudget
    from examples.docworkbench.experiment import ExperimentConfig, run_experiment
    from examples.docworkbench.generator import generate_corpus

    raw = json.loads(Path(config).read_text())
    cfg = ExperimentConfig(
        seeds=raw.get("seeds", [1]),
        budget_w=ExplorationBudget(max_actions=raw.get("max_actions", 4)),
        practice_ratio=raw.get("practice_ratio", 0.5),
        corpus_factory=lambda seed: generate_corpus(
            seed, raw.get("size", 8), raw.get("difficulty", 0.4)
        ),
        workdir=Path(out).parent / "workdirs",
        use_llm_reflector=raw.get("llm_reflector", False),
    )
    report = asyncio.run(run_experiment(cfg))
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2))
    for name, arm in report.arms.items():
        click.echo(f"{name}: mean={arm.mean:.3f} var={arm.variance:.3f} spent={arm.budget_spent}")
    click.echo(f"comparable={report.comparable} → {path}")


@cli.command("show-run")
@click.argument("run_id")
@click.option("--log", default="runtime_data/log.db", help="事件日志 db 路径")
def show_run(run_id: str, log: str) -> None:
    """打印一次 Run 的事件时间线。"""
    from agent_core.runtime.eventlog import SqliteEventLog

    event_log = SqliteEventLog(log)

    async def _render():
        rows = []
        async for e in event_log.iter_events(run_id):
            ts = e.ts.strftime("%H:%M:%S")
            rows.append(f"{ts} {e.type:22} {e.subject_type}:{e.subject_id}")
        return rows

    rows = asyncio.run(_render())
    if not rows:
        click.echo(f"(run {run_id} 无事件)")
        return
    for row in rows:
        click.echo(row)


@cli.command("show-memory")
@click.option("--db", default="runtime_data/mem.db", help="记忆库 db 路径")
@click.option("--ns", default="rules", help="命名空间")
def show_memory(db: str, ns: str) -> None:
    """列出命名空间下的记忆条目。"""
    from agent_core.memory.store import SqliteMemoryStore

    store = SqliteMemoryStore(db)

    async def _render():
        return await store.query(ns, {})

    records = asyncio.run(_render())
    if not records:
        click.echo(f"(namespace {ns} 为空)")
        return
    for r in records:
        stmt = r.content.get("statement", str(r.content))
        click.echo(f"[{r.verification.value}/{r.lifecycle.value}] r{r.revision} {stmt}")


if __name__ == "__main__":
    cli()

"""同预算对照实验（spec §7.1）——explore 臂 vs retry 臂，保留集评测。"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_core.models import ModelAdapter
from agent_explore.controller import ExplorationPolicy
from agent_explore.curriculum import ExplorationBudget
from pydantic import BaseModel

from examples.docworkbench.app import build_app
from examples.docworkbench.corpus import Corpus, split_corpus
from examples.docworkbench.generator import generate_corpus

DIMS = ["export", "extract", "convert", "validate"]


@dataclass
class ExperimentConfig:
    seeds: list[int]
    budget_w: ExplorationBudget
    practice_ratio: float = 0.5
    corpus_factory: Callable[[int], Corpus] = lambda seed: generate_corpus(seed, 8, 0.4)
    adapter_factory: Callable[[int], ModelAdapter] | None = None
    workdir: Path | None = None
    max_waves: int = 4


class ArmReport(BaseModel):
    name: str
    scores: list[float] = []
    mean: float = 0.0
    variance: float = 0.0
    per_dimension: dict[str, dict] = {}
    budget_spent: dict = {}
    attempts: int = 0


class ExperimentReport(BaseModel):
    seeds: list[int]
    arms: dict[str, ArmReport] = {}
    comparable: bool = False


class _DefaultAdapter:
    """默认适配器：课程请求返回单练习任务波次，其余交白卷。"""

    def __init__(self, plan_task_id: str) -> None:
        self._plan_task_id = plan_task_id

    def capabilities(self):
        from agent_core.models import Modality, ModelCapabilities

        return ModelCapabilities(model_id="m-app", inputs={Modality.TEXT})

    async def infer(self, request):
        import json as _json

        from agent_core.models import InferResponse

        if request.structured_output is not None:
            return InferResponse(
                text=_json.dumps(
                    {
                        "wave": {
                            "shared_memory_snapshot_id": "x",
                            "task_ids": [],
                            "parallelism": 1,
                        },
                        "tasks": [
                            {
                                "id": self._plan_task_id,
                                "family": "prerequisite",
                                "title": "练习",
                                "objective": {},
                                "acceptance": {
                                    "verifier_profile": "p",
                                    "required_evidence": [],
                                    "pass_conditions": [],
                                    "fail_conditions": [],
                                },
                            }
                        ],
                    }
                )
            )
        return InferResponse(text="no script")


def _default_adapter_factory(corpus_factory, practice_ratio: float):
    def factory(seed: int) -> ModelAdapter:
        corpus = corpus_factory(seed)
        tasks = corpus.tasks
        return _DefaultAdapter(plan_task_id=tasks[0]["id"])

    return factory


async def _eval_heldout(app, heldout) -> list[float]:
    scores: list[float] = []
    for task in heldout.tasks:
        run = await app.solve(task)
        scores.append(1.0 if run.state.value == "succeeded" else 0.0)
    return scores


def _arm_stats(
    name: str, scores: list[float], tasks: list[dict], spent: dict, attempts: int
) -> ArmReport:
    per_dim: dict[str, dict] = {}
    dim_of = {t["id"]: t["dimension"] for t in tasks}
    for dim in DIMS:
        ds = [s for t, s in zip(tasks, scores, strict=False) if dim_of[t["id"]] == dim]
        per_dim[dim] = {
            "mean": statistics.mean(ds) if ds else 0.0,
            "variance": statistics.pvariance(ds) if len(ds) > 1 else 0.0,
        }
    return ArmReport(
        name=name,
        scores=scores,
        mean=statistics.mean(scores) if scores else 0.0,
        variance=statistics.pvariance(scores) if len(scores) > 1 else 0.0,
        per_dimension=per_dim,
        budget_spent=spent,
        attempts=attempts,
    )


async def _explore_arm(config: ExperimentConfig, seed: int, corpus, practice, heldout) -> ArmReport:
    workdir = (config.workdir or Path(".")) / f"seed{seed}" / "explore"
    workdir.mkdir(parents=True, exist_ok=True)
    adapter_factory = config.adapter_factory or _default_adapter_factory(
        config.corpus_factory, config.practice_ratio
    )
    adapter = adapter_factory(seed)
    app = build_app(
        model_adapter=adapter,
        db_path=workdir / "app.db",
        blob_dir=workdir / "blobs",
        corpus=corpus,
        practice_part=practice,
    )
    report = await app.explore(
        ExplorationPolicy(mode="broad", budget=config.budget_w, max_waves=config.max_waves)
    )
    scores = await _eval_heldout(app, heldout)
    tasks = heldout.tasks
    return _arm_stats("explore", scores, tasks, report.budget_spent, report.tasks_executed)


async def _retry_arm(config: ExperimentConfig, seed: int, corpus, practice, heldout) -> ArmReport:
    """等量预算直接反复 target_attempt（不写长期记忆），再评保留集。"""
    from agent_explore.curriculum import BudgetMeter

    workdir = (config.workdir or Path(".")) / f"seed{seed}" / "retry"
    workdir.mkdir(parents=True, exist_ok=True)
    adapter_factory = config.adapter_factory or _default_adapter_factory(
        config.corpus_factory, config.practice_ratio
    )
    adapter = adapter_factory(seed)
    app = build_app(
        model_adapter=adapter,
        db_path=workdir / "app.db",
        blob_dir=workdir / "blobs",
        corpus=corpus,
        practice_part=practice,
    )
    meter = BudgetMeter(config.budget_w)
    attempts = 0
    i = 0
    practice_tasks = practice.tasks or corpus.tasks
    while not meter.status.exhausted:
        task = practice_tasks[i % len(practice_tasks)]
        await app.solve(task)  # 直接重试：不写长期记忆（app 只在 explore 时写入）
        meter.consume(actions=1, tokens=1)
        attempts += 1
        i += 1
        if attempts > 1000:  # 安全阀
            break
    scores = await _eval_heldout(app, heldout)
    return _arm_stats("retry", scores, heldout.tasks, meter.status.spent, attempts)


async def run_experiment(config: ExperimentConfig) -> ExperimentReport:
    all_scores: dict[str, list[float]] = {"explore": [], "retry": []}
    all_tasks: list[dict] = []
    spent: dict[str, dict] = {"explore": {}, "retry": {}}
    attempts: dict[str, int] = {"explore": 0, "retry": 0}

    for seed in config.seeds:
        corpus = config.corpus_factory(seed)
        practice, heldout = split_corpus(corpus, config.practice_ratio)
        all_tasks = heldout.tasks

        explore = await _explore_arm(config, seed, corpus, practice, heldout)
        retry = await _retry_arm(config, seed, corpus, practice, heldout)

        all_scores["explore"].extend(explore.scores)
        all_scores["retry"].extend(retry.scores)
        for dim in ("actions", "tokens", "cost", "wall_clock_s"):
            spent["explore"][dim] = spent["explore"].get(dim, 0) + explore.budget_spent.get(dim, 0)
            spent["retry"][dim] = spent["retry"].get(dim, 0) + retry.budget_spent.get(dim, 0)
        attempts["explore"] += explore.attempts
        attempts["retry"] += retry.attempts

    arms = {
        name: _arm_stats(name, scores, all_tasks, spent[name], attempts[name])
        for name, scores in all_scores.items()
    }
    comparable = spent["explore"].get("actions", 0) == spent["retry"].get("actions", 0)
    return ExperimentReport(seeds=config.seeds, arms=arms, comparable=comparable)

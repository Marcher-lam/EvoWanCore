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
    use_llm_reflector: bool = False  # V2 B 检查点：真 LLM 反思（T10）
    use_fake_vlm: bool = False  # v3 感知实验：WorkbenchFakeVLM 读图（T23）
    real_vlm_model: str | None = None  # 真 VLM 复跑：LM Studio 模型名（§6.1 可选）


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
    """环境配置了供应商 → 真适配器；否则退化为脚本占位（统计结构自检用）。"""
    import os

    base_url = os.environ.get("EWC_BASE_URL")
    api_key = os.environ.get("EWC_API_KEY")
    model_id = os.environ.get("EWC_MODEL_CHAT")

    if base_url and api_key and model_id:
        from agent_adapters.openai_compat import OpenAICompatAdapter
        from agent_core.models import Modality, ModelCapabilities

        def real_factory(seed: int) -> ModelAdapter:
            return OpenAICompatAdapter(
                base_url=base_url,
                api_key=api_key,
                capabilities=ModelCapabilities(
                    model_id=model_id,
                    inputs={Modality.TEXT},
                    interaction={"batch", "streaming"},
                    tools={"native_tool_call"},
                ),
            )

        return real_factory

    def factory(seed: int) -> ModelAdapter:
        corpus = corpus_factory(seed)
        tasks = corpus.tasks
        return _DefaultAdapter(plan_task_id=tasks[0]["id"])

    return factory


async def _eval_heldout(app, heldout) -> list[tuple[dict, float]]:
    pairs: list[tuple[dict, float]] = []
    for task in heldout.tasks:
        run = await app.solve(task)
        pairs.append((task, 1.0 if run.state.value == "succeeded" else 0.0))
    return pairs


def _arm_stats(
    name: str, pairs: list[tuple[dict, float]], spent: dict, attempts: int
) -> ArmReport:
    """pairs: (task, score) 对——跨种子收集，分组按 task 自带 dimension。"""
    scores = [s for _, s in pairs]
    per_dim: dict[str, dict] = {}
    for dim in DIMS:
        ds = [s for t, s in pairs if t["dimension"] == dim]
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


def _vlm_factory(config: ExperimentConfig):
    if config.real_vlm_model is not None:
        from examples.docworkbench.lmstudio_vlm import LMStudioVLM

        model = config.real_vlm_model
        return lambda corpus, derived: LMStudioVLM(model)
    if not config.use_fake_vlm:
        return None
    from examples.docworkbench.fake_vlm import WorkbenchFakeVLM

    return lambda corpus, derived: WorkbenchFakeVLM(corpus, derived)


async def _explore_arm(
    config: ExperimentConfig, seed: int, corpus, practice, heldout
) -> tuple[ArmReport, list[tuple[dict, float]]]:
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
        vlm_factory=_vlm_factory(config),
    )
    if config.use_llm_reflector:
        from agent_explore.reflector import LLMReflector

        # 共享 app gateway：reflector 调用经 CountingAdapter 计入真实预算（§6.2）
        app.use_reflector(LLMReflector(gateway=app.gateway, model_id="m-app"))
    report = await app.explore(
        ExplorationPolicy(mode="broad", budget=config.budget_w, max_waves=config.max_waves)
    )
    pairs = await _eval_heldout(app, heldout)
    return _arm_stats("explore", pairs, report.budget_spent, report.tasks_executed), pairs


async def _retry_arm(
    config: ExperimentConfig, seed: int, corpus, practice, heldout
) -> tuple[ArmReport, list[tuple[dict, float]]]:
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
        heldout_part=heldout,
        vlm_factory=_vlm_factory(config),
    )
    meter = BudgetMeter(config.budget_w)
    counter = app.model_counter  # §6.2：重试臂计真实 solve 调用
    last_calls = counter.calls if counter else 0
    attempts = 0
    i = 0
    practice_tasks = practice.tasks or corpus.tasks
    while not meter.status.exhausted:
        task = practice_tasks[i % len(practice_tasks)]
        await app.solve(task)  # 直接重试：不写长期记忆（app 只在 explore 时写入）
        meter.consume(
            actions=1,
            tokens=(counter.calls - last_calls) if counter else 0,
        )
        if counter:
            last_calls = counter.calls
        attempts += 1
        i += 1
        if attempts > 1000:  # 安全阀
            break
    pairs = await _eval_heldout(app, heldout)
    return _arm_stats("retry", pairs, meter.status.spent, attempts), pairs


async def run_experiment(config: ExperimentConfig) -> ExperimentReport:
    all_pairs: dict[str, list[tuple[dict, float]]] = {"explore": [], "retry": []}
    spent: dict[str, dict] = {"explore": {}, "retry": {}}
    attempts: dict[str, int] = {"explore": 0, "retry": 0}

    for seed in config.seeds:
        corpus = config.corpus_factory(seed)
        practice, heldout = split_corpus(corpus, config.practice_ratio)

        explore, explore_pairs = await _explore_arm(config, seed, corpus, practice, heldout)
        retry, retry_pairs = await _retry_arm(config, seed, corpus, practice, heldout)

        all_pairs["explore"].extend(explore_pairs)
        all_pairs["retry"].extend(retry_pairs)
        for dim in ("actions", "tokens", "cost", "wall_clock_s"):
            spent["explore"][dim] = spent["explore"].get(dim, 0) + explore.budget_spent.get(dim, 0)
            spent["retry"][dim] = spent["retry"].get(dim, 0) + retry.budget_spent.get(dim, 0)
        attempts["explore"] += explore.attempts
        attempts["retry"] += retry.attempts

    arms = {
        name: _arm_stats(name, pairs, spent[name], attempts[name])
        for name, pairs in all_pairs.items()
    }
    # C1（§6.2）：W 以真实调用计数计量（max_tokens=每种子调用预算）时，
    # comparable = 两臂都打满同一 W；否则回退 V1 动作等量口径
    w_calls = config.budget_w.max_tokens
    if w_calls is not None:
        need = w_calls * len(config.seeds)
        comparable = (
            spent["explore"].get("tokens", 0) >= need
            and spent["retry"].get("tokens", 0) >= need
        )
    else:
        comparable = (
            spent["explore"].get("actions", 0) == spent["retry"].get("actions", 0)
        )
    return ExperimentReport(seeds=config.seeds, arms=arms, comparable=comparable)

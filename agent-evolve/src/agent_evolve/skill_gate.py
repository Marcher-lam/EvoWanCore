"""skill_gate — 技能候选的先决契约门 + 演化门（V3 §5.2 B 块，RF#2 钉点）。

技能代码比记忆更危险：契约测试（子进程实测声明行为）不过 → SkillContractViolation，
连父子对比都不进；过了才走 EvolutionController 同款父子对比 + 回归检查。
"""

from __future__ import annotations

import statistics
from collections.abc import Awaitable, Callable

from agent_core.task import TaskSpec
from pydantic import BaseModel

from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox

DualRunner = Callable[[TaskSpec, bool], Awaitable[float]]  # (task, with_candidate)


class SkillContractViolation(Exception):
    """契约声明与实际行为不符——技能候选的先决门失败。"""


class EvalReport(BaseModel):
    mini_batch_scores: list[float]
    parent_scores: list[float] | None = None
    improved: bool = False
    regression_violations: list[str] = []


async def evaluate_skill(
    candidate: SkillArtifact,
    mini_batch: list[TaskSpec],
    runner: DualRunner,
    *,
    sandbox: SkillSandbox,
    regression_scenarios: list[TaskSpec] | None = None,
) -> EvalReport:
    """契约门（先决）→ 父子对比 + 回归（与 EvolutionController.evaluate 同语义）。"""
    contract = await sandbox.contract_test(candidate)
    if not contract.passed:
        failed = [c["name"] for c in contract.cases if not c["ok"]]
        raise SkillContractViolation(
            f"技能契约不符（实测未达声明行为）: {failed}——不进父子对比"
        )

    candidate_scores = [await runner(t, with_candidate=True) for t in mini_batch]
    parent_scores = [await runner(t, with_candidate=False) for t in mini_batch]

    violations: list[str] = []
    for t in regression_scenarios or []:
        base = await runner(t, with_candidate=False)
        with_cand = await runner(t, with_candidate=True)
        if with_cand < base:
            violations.append(t.id)

    cand_mean = statistics.mean(candidate_scores) if candidate_scores else 0.0
    parent_mean = statistics.mean(parent_scores) if parent_scores else 0.0
    improved = cand_mean > parent_mean or (not parent_scores and cand_mean > 0)
    return EvalReport(
        mini_batch_scores=candidate_scores,
        parent_scores=parent_scores,
        improved=improved,
        regression_violations=violations,
    )

"""练习对象模型：任务族、验收规格、波次、诊断类型（spec §5.3/BRS-DRS）。"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class TaskFamily(StrEnum):
    PREREQUISITE = "prerequisite"
    VARIANT = "variant"
    CONTRAST = "contrast"
    STRESS = "stress"
    TARGET_ATTEMPT = "target_attempt"
    REGRESSION = "regression"


class DiagnosisType(StrEnum):
    MISSING_PREREQUISITE = "missing_prerequisite"
    WRONG_CAUSAL_MODEL = "wrong_causal_model"
    OVERGENERALIZED_RULE = "overgeneralized_rule"
    VERIFICATION_GAP = "verification_gap"
    FRAGILE_SUCCESS = "fragile_success"


# 诊断 → 下一步任务族（DRS 的核心映射）
DIAGNOSIS_FAMILY_MAP: dict[str, TaskFamily] = {
    DiagnosisType.MISSING_PREREQUISITE: TaskFamily.PREREQUISITE,
    DiagnosisType.WRONG_CAUSAL_MODEL: TaskFamily.CONTRAST,
    DiagnosisType.OVERGENERALIZED_RULE: TaskFamily.CONTRAST,
    DiagnosisType.FRAGILE_SUCCESS: TaskFamily.STRESS,
    DiagnosisType.VERIFICATION_GAP: TaskFamily.VARIANT,
}


class AcceptanceSpec(BaseModel):
    """探索任务自带验收条件——"了解这个软件"不算练习，可验收的才算。"""

    verifier_profile: str
    required_evidence: list[str] = []
    pass_conditions: list[dict] = []
    fail_conditions: list[dict] = []


class PracticeTask(BaseModel):
    id: str = Field(default_factory=lambda: f"pt_{uuid.uuid4().hex[:12]}")
    family: TaskFamily
    title: str
    objective: dict
    setup: dict = {}
    constraints: dict = {}
    acceptance: AcceptanceSpec  # 必填：无验收条件的练习不允许存在
    priority: float = 0.5
    difficulty: float = 0.5
    depends_on: list[str] = []
    tags: list[str] = []


class PracticeWave(BaseModel):
    """BRS 波次：多任务从同一记忆快照并行出发，批完成后按序合并。"""

    wave_id: str = Field(default_factory=lambda: f"wave_{uuid.uuid4().hex[:12]}")
    shared_memory_snapshot_id: str
    task_ids: list[str]
    parallelism: int = 4
    merge_policy: Literal["sequential"] = "sequential"

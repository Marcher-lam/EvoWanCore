"""TaskSpec / TaskHandle — 任务定义与实例（spec §5.3）。

TaskSpec 是定义（目标、schema、约束、验收条件）；TaskHandle 是运行中的实例。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class TaskSpec(BaseModel):
    id: str
    kind: str
    goal: dict
    input_schema: dict
    output_schema: dict | None = None
    constraints: dict = {}
    acceptance_criteria: list[dict]

    @field_validator("acceptance_criteria")
    @classmethod
    def _nonempty_acceptance(cls, v: list[dict]) -> list[dict]:
        if not v:
            raise ValueError("acceptance_criteria 不得为空：无验收条件的任务无法判定完成")
        return v


class TaskHandle(BaseModel):
    id: str
    spec_id: str
    parent_task_id: str | None = None
    assigned_agent: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

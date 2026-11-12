"""SDK 请求/响应模型——复用内核 Pydantic 契约。"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class SubmitRunRequest(BaseModel):
    task_id: str
    kind: str
    goal: dict = {}
    input_schema: dict = {}
    constraints: dict = {}
    acceptance_criteria: list[dict] = [{"check": "done"}]
    agent_id: str = "default"


class RunStatusResponse(BaseModel):
    run_id: str
    state: str
    task_id: str


class EventResponse(BaseModel):
    id: str
    type: str
    ts: str
    subject_type: str
    subject_id: str
    payload: dict = {}


class CancelRunResponse(BaseModel):
    run_id: str
    cancelled: bool


class ExploreRequest(BaseModel):
    mode: str = "broad"
    max_waves: int | None = 2


class PurgeResponse(BaseModel):
    record_id: str
    deleted_revisions: int
    reason: str


class ResumeRunRequest(BaseModel):
    """resume 注入协议：doc_id → image URL（corpus 在 caller 侧，内核只存引用）。

    vision 任务恢复时，内核用 image_urls[checkpoint.image_doc_id] 重建
    constraints.image_url；无映射则保留标记降级。
    URL 约束：scheme 白名单（http/https/data:image）+ 单值长度上限——
    注入的 URL 会复制进该 run 的每一次多模态 infer 消息，必须限幅。
    """

    image_urls: dict[str, str] = {}

    @field_validator("image_urls")
    @classmethod
    def _validate_urls(cls, v: dict[str, str]) -> dict[str, str]:
        allowed = ("http://", "https://", "data:image/")
        for key, url in v.items():
            if not url.startswith(allowed):
                raise ValueError(
                    f"image_urls[{key}] scheme 不在白名单 {allowed}: {url[:50]}"
                )
            if len(url) > 2_000_000:
                raise ValueError(f"image_urls[{key}] 超过 2MB 上限")
        return v


class ExploreResponse(BaseModel):
    waves_executed: int = 0
    tasks_executed: int = 0
    tasks_passed: int = 0
    candidates_generated: int = 0
    budget_spent: dict = {}

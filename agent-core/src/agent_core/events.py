"""RuntimeEvent — 运行时统一事件基元（spec §5.3）。

append-only 事件日志的最小单元：谁（subject）、何时（ts）、因谁（causation）、
属哪条因果链（correlation）、带了什么事实（payload）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SubjectType = Literal["task", "run", "action", "agent", "gap", "memory", "release"]


class RuntimeEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"ev_{uuid.uuid4().hex}")
    type: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    subject_type: SubjectType
    subject_id: str
    causation_id: str | None = None
    correlation_id: str | None = None
    payload: dict = {}


def new_event(
    type: str,
    subject_type: str,
    subject_id: str,
    *,
    causation_id: str | None = None,
    correlation_id: str | None = None,
    **payload: object,
) -> RuntimeEvent:
    """构造事件的便捷入口；多余关键字全部落入 payload。"""
    return RuntimeEvent(
        type=type,
        subject_type=subject_type,
        subject_id=subject_id,
        causation_id=causation_id,
        correlation_id=correlation_id,
        payload=dict(payload),
    )

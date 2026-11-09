"""T3 RuntimeEvent 基元。"""

import json
from datetime import UTC, datetime

from agent_core.events import RuntimeEvent, new_event


def test_new_event_defaults() -> None:
    ev = new_event("TaskCreated", "task", "task_1", goal={"kind": "parse"})
    assert isinstance(ev, RuntimeEvent)
    assert ev.id.startswith("ev_")
    assert len(ev.id) == 3 + 32  # 前缀 + uuid4hex
    assert ev.type == "TaskCreated"
    assert ev.subject_type == "task"
    assert ev.subject_id == "task_1"
    assert ev.causation_id is None
    assert ev.correlation_id is None
    assert ev.payload == {"goal": {"kind": "parse"}}
    assert isinstance(ev.ts, datetime)
    assert ev.ts.tzinfo is not None  # 带时区


def test_new_event_links_causality() -> None:
    ev = new_event(
        "ActionDispatched",
        "action",
        "act_1",
        causation_id="ev_prev",
        correlation_id="corr_9",
        status="ok",
    )
    assert ev.causation_id == "ev_prev"
    assert ev.correlation_id == "corr_9"
    assert ev.payload["status"] == "ok"


def test_event_is_pydantic_json_serializable() -> None:
    ev = new_event(
        "ActionVerified", "action", "act_2", checks=[{"name": "file_exists", "passed": True}]
    )
    restored = RuntimeEvent.model_validate_json(ev.model_dump_json())
    assert restored == ev
    data = json.loads(ev.model_dump_json())
    assert data["subject_type"] == "action"
    assert data["payload"]["checks"][0]["passed"] is True


def test_subject_type_is_constrained() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeEvent(
            id="ev_x",
            type="Weird",
            ts=datetime.now(UTC),
            subject_type="banana",  # 非法主体类型
            subject_id="s1",
            causation_id=None,
            correlation_id=None,
            payload={},
        )

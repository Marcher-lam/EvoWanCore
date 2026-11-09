"""T6 能力目录 + 策略引擎：副作用门禁、授权范围、预算。"""

import pytest
from agent_core.action import ActionMode, ActionRequest
from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)
from agent_core.policy import DefaultPolicyEngine


def _desc(**over) -> CapabilityDescriptor:
    base = dict(
        id="tool_export",
        type=CapabilityType.TOOL,
        name="导出文档",
        input_schema={"doc": "str"},
        output_schema={"file": "str"},
        side_effect_level="write",
        auth_scope=["fs:write"],
        version="1.0.0",
        tags=["document", "export"],
    )
    base.update(over)
    return CapabilityDescriptor(**base)


def _action(capability_id: str = "tool_export") -> ActionRequest:
    return ActionRequest(
        id="act_1",
        task_id="task_1",
        run_id="run_1",
        actor_id="agent_1",
        capability_id=capability_id,
        arguments={},
        mode=ActionMode.EXECUTE,
    )


# ---------- Registry ----------


def test_registry_register_discover_resolve() -> None:
    reg = InMemoryCapabilityRegistry()
    reg.register(_desc())
    reg.register(_desc(id="skill_parse", type=CapabilityType.SKILL, tags=["parse"]))

    found = reg.discover({"type": CapabilityType.TOOL})
    assert [d.id for d in found] == ["tool_export"]

    found = reg.discover({"tags": ["parse"]})
    assert [d.id for d in found] == ["skill_parse"]

    assert reg.resolve("tool_export").name == "导出文档"
    with pytest.raises(KeyError):
        reg.resolve("nope")


def test_discover_empty_query_returns_all() -> None:
    reg = InMemoryCapabilityRegistry()
    reg.register(_desc())
    reg.register(_desc(id="skill_parse", type=CapabilityType.SKILL))
    assert len(reg.discover({})) == 2


# ---------- PolicyEngine ----------


def test_irreversible_requires_approval() -> None:
    engine = DefaultPolicyEngine()
    decision = engine.authorize(
        _action("tool_delete"),
        {
            "capability": _desc(id="tool_delete", side_effect_level="irreversible"),
            "granted_scopes": ["fs:write"],
        },
    )
    assert decision.allowed is True
    assert decision.requires_approval is True


def test_read_side_effect_no_approval() -> None:
    engine = DefaultPolicyEngine()
    decision = engine.authorize(
        _action(), {"capability": _desc(side_effect_level="read", auth_scope=[])}
    )
    assert decision.allowed is True
    assert decision.requires_approval is False


def test_missing_auth_scope_denied() -> None:
    engine = DefaultPolicyEngine()
    decision = engine.authorize(
        _action(), {"capability": _desc(auth_scope=["fs:write"]), "granted_scopes": ["fs:read"]}
    )
    assert decision.allowed is False
    assert "fs:write" in decision.reason


def test_budget_exceeded_denied() -> None:
    engine = DefaultPolicyEngine(budget_limits={"tokens": 1000})
    ok = engine.enforce_budget("run_1", {"tokens": 900})
    assert ok.allowed is True
    over = engine.enforce_budget("run_1", {"tokens": 1001})
    assert over.allowed is False
    assert "tokens" in over.reason


def test_unknown_capability_denied() -> None:
    engine = DefaultPolicyEngine()
    decision = engine.authorize(_action(), {})  # 无 capability 描述
    assert decision.allowed is False

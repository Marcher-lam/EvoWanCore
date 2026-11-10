"""T27 MCPServerExporter：只读子集暴露 + 每调用过 PolicyEngine（Review Focus #4）。"""

import pytest
from agent_adapters.mcp_server import MCPServerExporter
from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)
from agent_core.policy import DefaultPolicyEngine
from agent_core.runtime.executor import CapabilityExecutor


def _registry_with_tools() -> InMemoryCapabilityRegistry:
    registry = InMemoryCapabilityRegistry()

    async def safe_handler(args):  # noqa: ARG001
        return {"ok": True}

    async def danger_handler(args):  # noqa: ARG001
        return {"destroyed": True}

    def bind(id_, side_effect, scope, handler):
        registry.bind(
            CapabilityDescriptor(
                id=id_,
                type=CapabilityType.TOOL,
                name=id_,
                input_schema={"doc_id": "str"},
                output_schema={},
                side_effect_level=side_effect,
                auth_scope=scope,
                tags=["workbench"],
            ),
            handler,
        )

    bind("wb_read", "read", [], safe_handler)
    bind("wb_write", "write", [], safe_handler)
    bind("wb_nuke", "irreversible", [], danger_handler)
    bind("wb_scoped", "read", ["secret:read"], safe_handler)
    return registry


def test_exports_only_safe_subset() -> None:
    """可暴露集 = 非 irreversible 且 auth_scope 空且（allowed None 或 id ∈ allowed）。"""
    registry = _registry_with_tools()
    exporter = MCPServerExporter(
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        executor=CapabilityExecutor(registry=registry, skill_runner=None),
    )
    exported = {d["id"] for d in exporter.exportable()}
    assert exported == {"wb_read", "wb_write"}, "irreversible/带 scope 不得暴露"

    restricted = MCPServerExporter(
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        executor=CapabilityExecutor(registry=registry, skill_runner=None),
        allowed={"wb_read"},
    )
    assert [d["id"] for d in restricted.exportable()] == ["wb_read"], "白名单收窄暴露集"


async def test_exported_tool_call_roundtrip() -> None:
    """导出的工具经 exporter 调用：ActionRequest→authorize→dispatch→结果回传。"""
    registry = _registry_with_tools()
    exporter = MCPServerExporter(
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        executor=CapabilityExecutor(registry=registry, skill_runner=None),
        allowed={"wb_read"},
    )
    result = await exporter.call_exported("wb_read", {"doc_id": "doc_1"})
    assert result == {"ok": True}


async def test_irreversible_rejected_per_call() -> None:
    """Review Focus #4：绕过暴露面直呼不可逆/越权能力，逐调用 authorize 拒且不执行。"""
    registry = _registry_with_tools()
    exporter = MCPServerExporter(
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        executor=CapabilityExecutor(registry=registry, skill_runner=None),
    )
    with pytest.raises(PermissionError, match="不可逆|irreversible|审批"):
        await exporter.call_exported("wb_nuke", {"doc_id": "doc_1"})
    with pytest.raises(PermissionError, match="scope|授权"):
        await exporter.call_exported("wb_scoped", {"doc_id": "doc_1"})

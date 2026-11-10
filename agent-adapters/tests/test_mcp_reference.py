"""T26 MCP client 参考验收：fixture server → import → registry → executor 派发往返。

外部 MCP 工具经统一能力目录被 CapabilityExecutor 正常派发，结果进 ActionResult。
"""

import sys
from pathlib import Path

import pytest
from agent_adapters.mcp_client import MCPClientAdapter
from agent_core.action import ActionMode, ActionRequest
from agent_core.capability import InMemoryCapabilityRegistry
from agent_core.runtime.executor import CapabilityExecutor


@pytest.fixture
def fs_root(tmp_path: Path) -> Path:
    (tmp_path / "invoice.txt").write_text("INV-2026-001 金额 42.00", encoding="utf-8")
    return tmp_path


async def test_mcp_client_reference_roundtrip(fs_root: Path) -> None:
    """端到端：read_text 经 executor 派发，输出与 tmp 文件逐字一致。"""
    registry = InMemoryCapabilityRegistry()
    adapter = MCPClientAdapter(
        [sys.executable, "agent-adapters/tests/fixtures/fs_server.py", str(fs_root)]
    )
    try:
        bound = await adapter.bind_all(registry)
        assert "mcp_read_text" in bound

        executor = CapabilityExecutor(registry=registry, skill_runner=None)
        result = await executor.dispatch(
            ActionRequest(
                id="act_mcp_read",
                task_id="task_ref",
                run_id="run_ref",
                actor_id="agent_mcp",
                capability_id="mcp_read_text",
                arguments={"path": "invoice.txt"},
                mode=ActionMode.EXECUTE,
            )
        )
        assert result.status.value == "succeeded"
        assert result.output["text"] == "INV-2026-001 金额 42.00"
        assert result.action_id == "act_mcp_read"
    finally:
        await adapter.close()


async def test_mcp_tool_error_is_failed_action(fs_root: Path) -> None:
    """工具侧错误（路径逃逸）→ ActionResult FAILED 且含错误信息，不抛异常。"""
    registry = InMemoryCapabilityRegistry()
    adapter = MCPClientAdapter(
        [sys.executable, "agent-adapters/tests/fixtures/fs_server.py", str(fs_root)]
    )
    try:
        await adapter.bind_all(registry)
        executor = CapabilityExecutor(registry=registry, skill_runner=None)
        result = await executor.dispatch(
            ActionRequest(
                id="act_mcp_escape",
                task_id="task_ref",
                run_id="run_ref",
                actor_id="agent_mcp",
                capability_id="mcp_read_text",
                arguments={"path": "../../etc/passwd"},
                mode=ActionMode.EXECUTE,
            )
        )
        assert result.status.value == "failed"
        assert result.error and "路径逃逸" in str(result.error.get("reason", ""))
    finally:
        await adapter.close()

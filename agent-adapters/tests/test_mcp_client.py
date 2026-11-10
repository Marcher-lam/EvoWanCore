"""T24 MCPClientAdapter：list_tools→能力目录映射 + call_tool 绑定往返（fixture server）。"""

import sys
from pathlib import Path

import pytest
from agent_adapters.mcp_client import MCPClientAdapter
from agent_core.capability import InMemoryCapabilityRegistry


def _server_cmd(root: Path) -> list[str]:
    return [sys.executable, "agent-adapters/tests/fixtures/fs_server.py", str(root)]


@pytest.fixture
def fs_root(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello mcp roundtrip", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "note.md").write_text("nested", encoding="utf-8")
    return tmp_path


async def test_import_capabilities_maps_tools(fs_root: Path) -> None:
    """list_tools → CapabilityDescriptor：schema 映射、默认 read、mcp 前缀 id。"""
    adapter = MCPClientAdapter(_server_cmd(fs_root))
    try:
        caps = await adapter.import_capabilities()
        by_id = {d.id: d for d in caps}
        assert set(by_id) == {"mcp_list_dir", "mcp_read_text"}
        read_text = by_id["mcp_read_text"]
        assert read_text.type.value == "tool"
        assert read_text.side_effect_level == "read"
        assert "mcp" in read_text.tags
        assert isinstance(read_text.input_schema, dict) and read_text.input_schema, (
            "工具 inputSchema 必须映射进能力描述符"
        )
    finally:
        await adapter.close()


async def test_call_tool_roundtrip(fs_root: Path) -> None:
    """bind_all 注册的 handler 经注册表派发 call_tool，内容与 tmp 文件一致。"""
    registry = InMemoryCapabilityRegistry()
    adapter = MCPClientAdapter(_server_cmd(fs_root))
    try:
        bound = await adapter.bind_all(registry)
        assert set(bound) == {"mcp_list_dir", "mcp_read_text"}
        handler = registry.handler_for("mcp_read_text")
        assert handler is not None
        result = await handler({"path": "hello.txt"})
        assert result["text"] == "hello mcp roundtrip"
        listing = await registry.handler_for("mcp_list_dir")({})
        assert "hello.txt" in listing["text"] and "sub/" in listing["text"]
        nested = await handler({"path": "sub/note.md"})
        assert nested["text"] == "nested"
    finally:
        await adapter.close()

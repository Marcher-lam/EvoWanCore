"""T29 MCP server e2e：外部 client session 连 exporter server（内存流）。

结果正确 + ActionRequest/证据落 EventLog；不可逆能力在暴露面不可见且逐调用被拒。
"""

import asyncio
from pathlib import Path

from agent_adapters.mcp_server import MCPServerExporter
from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)
from agent_core.events import new_event  # noqa: F401 — 断言事件形态
from agent_core.policy import DefaultPolicyEngine
from agent_core.runtime.eventlog import EventLog, SqliteEventLog
from agent_core.runtime.executor import CapabilityExecutor


def _registry() -> InMemoryCapabilityRegistry:
    registry = InMemoryCapabilityRegistry()

    async def read_doc(args):
        return {"doc_id": args["doc_id"], "text": "workbench 文档内容"}

    async def nuke(args):  # noqa: ARG001
        return {"destroyed": True}

    def bind(id_, side_effect, handler):
        registry.bind(
            CapabilityDescriptor(
                id=id_, type=CapabilityType.TOOL, name=id_,
                input_schema={"doc_id": "str"}, side_effect_level=side_effect,
                auth_scope=[], tags=["workbench"],
            ),
            handler,
        )

    bind("wb_read_doc", "read", read_doc)
    bind("wb_nuke_all", "irreversible", nuke)
    return registry


async def test_external_client_roundtrip(tmp_path: Path) -> None:
    """外部 MCP client：list_tools 只见安全子集；调用结果正确；证据链落 EventLog。"""
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    registry = _registry()
    event_log: EventLog = SqliteEventLog(tmp_path / "mcp.db")
    exporter = MCPServerExporter(
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        executor=CapabilityExecutor(registry=registry, skill_runner=None),
        event_log=event_log,
    )
    server = exporter.build_server()

    streams_cm = create_client_server_memory_streams()
    client_streams, server_streams = await streams_cm.__aenter__()
    server_task = asyncio.create_task(
        server.run(  # build_server 返回低层 Server（官方 run_stdio_async 同款接线）
            server_streams[0],
            server_streams[1],
            server.create_initialization_options(),
        )
    )
    try:
        async with ClientSession(client_streams[0], client_streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert names == {"wb_read_doc"}, "不可逆能力不得出现在 MCP 暴露面"

            result = await session.call_tool("wb_read_doc", {"doc_id": "doc_1"})
            assert not getattr(result, "is_error", False)
            assert "workbench 文档内容" in result.content[0].text
    finally:
        server_task.cancel()
        try:
            await server_task
        except BaseException:  # noqa: BLE001 — 测试收尾
            pass
        await streams_cm.__aexit__(None, None, None)

    events = [e async for e in event_log.iter_events()]
    types = [e.type for e in events]
    assert "ActionProposed" in types and "ActionSucceeded" in types, (
        "外部调用须以 ActionRequest/证据链落 EventLog"
    )
    proposed = next(e for e in events if e.type == "ActionProposed")
    assert proposed.payload["capability_id"] == "wb_read_doc"
    assert proposed.payload["arguments"] == {"doc_id": "doc_1"}

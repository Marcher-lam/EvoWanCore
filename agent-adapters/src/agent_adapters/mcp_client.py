"""MCPClientAdapter — 外部 MCP server 工具进统一能力目录（V2 §5.3 C-MCP client）。

官方 Python SDK（stdio）。lazy session：首次使用时启动 server 子进程并握手，
close() 显式回收（子进程归入现有进程管理纪律）。
"""

from __future__ import annotations

from typing import Any

from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityType,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClientAdapter:
    """ProtocolAdapter 实现：list_tools → CapabilityDescriptor；call_tool 绑 handler。"""

    protocol_name = "mcp"

    def __init__(self, server_cmd: list[str]) -> None:
        self._cmd = list(server_cmd)
        self._session: ClientSession | None = None
        self._client_cm = None

    async def _ensure_session(self) -> ClientSession:
        if self._session is None:
            params = StdioServerParameters(
                command=self._cmd[0], args=self._cmd[1:]
            )
            self._client_cm = stdio_client(params)
            read, write = await self._client_cm.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
        return self._session

    async def close(self) -> None:
        """显式回收：session → 子进程（幂等）。"""
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._client_cm is not None:
            await self._client_cm.__aexit__(None, None, None)
            self._client_cm = None

    async def import_capabilities(self) -> list[CapabilityDescriptor]:
        """list_tools → 能力目录（inputSchema 映射；默认 read；可由描述符覆盖）。"""
        session = await self._ensure_session()
        tools = await session.list_tools()
        return [
            CapabilityDescriptor(
                id=f"mcp_{t.name}",
                type=CapabilityType.TOOL,
                name=t.name,
                input_schema=dict(getattr(t, "input_schema", None) or {}),
                output_schema={},
                side_effect_level="read",
                auth_scope=[],
                tags=["mcp", *([t.name] if t.name else [])],
            )
            for t in tools.tools
        ]

    async def bind_all(self, registry: CapabilityRegistry) -> list[str]:
        """每个工具绑 call_tool handler（lazy session 复用）。返回绑定的能力 id。"""
        bound_ids: list[str] = []
        descriptors = await self.import_capabilities()
        for d in descriptors:
            tool_name = d.id.removeprefix("mcp_")

            def _handler(args: dict, _tool: str = tool_name) -> Any:
                async def _call() -> dict:
                    session = await self._ensure_session()
                    result = await session.call_tool(_tool, dict(args))
                    if getattr(result, "isError", False):
                        texts = [
                            getattr(c, "text", str(c)) for c in (result.content or [])
                        ]
                        raise RuntimeError(f"MCP 工具 {_tool} 报错: {'; '.join(texts)}")
                    texts = [
                        getattr(c, "text", "") for c in (result.content or [])
                    ]
                    return {"text": "\n".join(t for t in texts if t)}

                return _call()

            registry.bind(d, _handler)
            bound_ids.append(d.id)
        return bound_ids

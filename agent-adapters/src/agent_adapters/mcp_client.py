"""MCPClientAdapter — 外部 MCP server 工具进统一能力目录（V2 §5.3 C-MCP client）。

官方 Python SDK（stdio）。session 由**专属 owner task** 持有（anyio 上下文管理器
必须在同一任务进出——并发调用方经队列代理），lazy 启动、close() 显式回收。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityType,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class _Call:
    """经 owner task 代理的一次 session 调用。"""

    def __init__(self, fn: Callable[..., Any], args: tuple) -> None:
        self.fn = fn
        self.args = args
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()


class MCPClientAdapter:
    """ProtocolAdapter 实现：list_tools → CapabilityDescriptor；call_tool 绑 handler。"""

    protocol_name = "mcp"

    def __init__(self, server_cmd: list[str]) -> None:
        self._cmd = list(server_cmd)
        self._session: ClientSession | None = None
        self._owner: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None
        self._ready: asyncio.Event | None = None
        self._error: BaseException | None = None
        self._lock = asyncio.Lock()  # I4：并发首调只起一个 server 子进程

    # ---------- session 生命周期 ----------

    async def _run_session(self) -> None:
        params = StdioServerParameters(command=self._cmd[0], args=self._cmd[1:])
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    while True:
                        call = await self._queue.get()
                        if call is None:
                            break
                        try:
                            call.future.set_result(await call.fn(session, *call.args))
                        except BaseException as e:  # noqa: BLE001 — 回传调用方
                            call.future.set_exception(e)
        except BaseException as e:  # noqa: BLE001 — 启动失败也要放行等待者
            self._error = e
            self._ready.set()

    async def _ensure_started(self) -> None:
        async with self._lock:
            if self._owner is None:
                self._queue = asyncio.Queue()
                self._ready = asyncio.Event()
                self._owner = asyncio.create_task(self._run_session())
        if self._ready is not None:
            await self._ready.wait()
        if self._error is not None:
            raise RuntimeError(f"MCP server 启动失败: {self._error}")

    async def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        await self._ensure_started()
        call = _Call(fn, args)
        await self._queue.put(call)
        return await call.future

    async def close(self) -> None:
        """显式回收：通知 owner task 退出（CM 在其自身任务内进出）。幂等。"""
        if self._owner is not None:
            await self._queue.put(None)
            await self._owner
            self._owner = None
            self._session = None

    # ---------- 能力目录 ----------

    async def import_capabilities(self) -> list[CapabilityDescriptor]:
        """list_tools → 能力目录（inputSchema 映射；默认 read）。"""
        tools = await self._call(lambda s: s.list_tools())
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
        """每个工具绑 call_tool handler（session 复用，调用经 owner 代理）。"""
        bound_ids: list[str] = []
        descriptors = await self.import_capabilities()
        for d in descriptors:
            tool_name = d.id.removeprefix("mcp_")

            def _handler(args: dict, _tool: str = tool_name) -> Any:
                return self._call_tool(_tool, dict(args))

            registry.bind(d, _handler)
            bound_ids.append(d.id)
        return bound_ids

    async def _call_tool(self, tool: str, args: dict) -> dict:
        result = await self._call(lambda s: s.call_tool(tool, args))
        is_error = bool(
            getattr(result, "is_error", None) or getattr(result, "isError", False)
        )
        texts = [getattr(c, "text", "") for c in (result.content or [])]
        if is_error:
            raise RuntimeError(
                f"MCP 工具 {tool} 报错: {'; '.join(t for t in texts if t)}"
            )
        return {"text": "\n".join(t for t in texts if t)}

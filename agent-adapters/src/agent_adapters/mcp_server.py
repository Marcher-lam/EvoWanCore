"""MCPServerExporter — Core 能力经 MCP 暴露（V2 §5.3 C-MCP server）。

暴露子集 = 非 irreversible 且 auth_scope 空（且白名单可选收窄）。
每个调用构造 ActionRequest 走 PolicyEngine.authorize + CapabilityExecutor——
复用全部门禁与证据链；不可逆/越权调用拒绝且不执行（Review Focus #4）。
"""

from __future__ import annotations

from typing import Any

from agent_core.action import ActionMode, ActionRequest
from agent_core.capability import CapabilityDescriptor, CapabilityRegistry
from agent_core.policy import PolicyEngine
from agent_core.runtime.executor import CapabilityExecutor


class MCPServerExporter:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        policy_engine: PolicyEngine,
        executor: CapabilityExecutor,
        allowed: set[str] | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy_engine
        self._executor = executor
        self._allowed = allowed

    def exportable(self) -> list[dict]:
        """可暴露能力清单（descriptor 摘要，供 MCP 工具 schema 生成）。"""
        out: list[dict] = []
        for d in self._registry.discover({}):
            if d.side_effect_level == "irreversible":
                continue
            if d.auth_scope:
                continue
            if self._allowed is not None and d.id not in self._allowed:
                continue
            out.append({"id": d.id, "name": d.name, "input_schema": d.input_schema})
        return out

    def build_server(self):
        """由可暴露集生成 MCP server（官方 SDK；工具 schema 出 descriptor）。"""
        from mcp.server.mcpserver import MCPServer

        server = MCPServer(name="evowancore-exporter")
        for spec in self.exportable():
            def _make_tool(tool_id: str):
                async def _tool(**kwargs: Any) -> Any:
                    return await self.call_exported(tool_id, dict(kwargs))

                _tool.__name__ = tool_id
                return _tool

            server.add_tool(
                _make_tool(spec["id"]),
                name=spec["id"],
                description=f"Core capability {spec['name']} (policy-gated)",
            )
        return server

    async def call_exported(self, capability_id: str, arguments: dict) -> Any:
        """逐调用门禁：ActionRequest → authorize（审批要求=拒）→ dispatch → 输出。"""
        try:
            capability: CapabilityDescriptor | None = self._registry.resolve(
                capability_id
            )
        except KeyError:
            capability = None
        action = ActionRequest(
            id=f"mcp_{capability_id}",
            task_id="task_mcp",
            run_id="run_mcp",
            actor_id="mcp_client",
            capability_id=capability_id,
            arguments=dict(arguments),
            mode=ActionMode.EXECUTE,
        )
        decision = self._policy.authorize(
            action, {"capability": capability, "granted_scopes": []}
        )
        if not decision.allowed:
            raise PermissionError(f"MCP 调用被策略拒绝: {decision.reason}")
        if decision.requires_approval:
            # MCP 线上无人审批：不可逆动作一律拒绝（宁拒不执行）
            raise PermissionError(
                f"MCP 调用需要审批（不可逆），线上拒绝执行: {decision.reason}"
            )
        result = await self._executor.dispatch(action)
        if result.status.value != "succeeded":
            raise RuntimeError(f"MCP 工具执行失败: {result.error}")
        return result.output

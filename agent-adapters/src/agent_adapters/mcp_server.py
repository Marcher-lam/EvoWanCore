"""MCPServerExporter — Core 能力经 MCP 暴露（V2 §5.3 C-MCP server）。

暴露子集 = 非 irreversible 且 auth_scope 空（且白名单可选收窄）。
每个调用构造 ActionRequest 走 PolicyEngine.authorize + CapabilityExecutor——
复用全部门禁与证据链；不可逆/越权调用拒绝且不执行（Review Focus #4）。
"""

from __future__ import annotations

from typing import Any

from agent_core.action import ActionMode, ActionRequest
from agent_core.capability import CapabilityDescriptor, CapabilityRegistry
from agent_core.events import new_event
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
        event_log=None,
    ) -> None:
        self._registry = registry
        self._policy = policy_engine
        self._executor = executor
        self._allowed = allowed
        self._event_log = event_log  # 可选：外部调用的 ActionRequest/证据链落档

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
        """由可暴露集生成低层 MCP server（工具 schema 出 descriptor，官方 SDK 2.x）。

        on_call_tool 复用 call_exported 的逐调用门禁（authorize + 证据链）。
        """
        import json as _json

        from mcp import types
        from mcp.server.lowlevel import Server

        def _json_schema(schema: dict) -> dict:
            """Core 描述符简写 schema → 合法 JSON Schema（MCP 要求 type: object）。"""
            if not isinstance(schema, dict) or not schema:
                return {"type": "object"}
            if "type" in schema:
                return schema
            return {
                "type": "object",
                "properties": {
                    k: {"type": "string"} for k in schema
                },
            }

        async def _list_tools(ctx, params=None):  # noqa: ARG001
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name=spec["id"],
                        description=f"Core capability {spec['name']} (policy-gated)",
                        inputSchema=_json_schema(spec["input_schema"]),
                    )
                    for spec in self.exportable()
                ]
            )

        async def _call_tool(ctx, params):
            try:
                output = await self.call_exported(
                    params.name, dict(params.arguments or {})
                )
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            type="text",
                            text=_json.dumps(output, ensure_ascii=False, default=str),
                        )
                    ]
                )
            except (PermissionError, RuntimeError) as e:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=str(e))],
                    is_error=True,
                )

        return Server(
            "evowancore-exporter",
            on_list_tools=_list_tools,
            on_call_tool=_call_tool,
        )

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
        proposed = new_event(
            "ActionProposed", "action", action.id,
            capability_id=capability_id, arguments=dict(arguments),
        )
        if self._event_log is not None:
            await self._event_log.append(proposed)
        decision = self._policy.authorize(
            action, {"capability": capability, "granted_scopes": []}
        )
        if not decision.allowed:
            if self._event_log is not None:
                await self._event_log.append(
                    new_event(
                        "ActionRejected", "action", action.id,
                        reason=decision.reason, correlation_id="run_mcp",
                        causation_id=proposed.id,
                    )
                )
            raise PermissionError(f"MCP 调用被策略拒绝: {decision.reason}")
        if decision.requires_approval:
            # MCP 线上无人审批：不可逆动作一律拒绝（宁拒不执行）
            if self._event_log is not None:
                await self._event_log.append(
                    new_event(
                        "ActionRejected", "action", action.id,
                        reason="requires_approval", correlation_id="run_mcp",
                        causation_id=proposed.id,
                    )
                )
            raise PermissionError(
                f"MCP 调用需要审批（不可逆），线上拒绝执行: {decision.reason}"
            )
        result = await self._executor.dispatch(action)
        if self._event_log is not None:
            event_type = "ActionSucceeded" if result.status.value == "succeeded" else "ActionFailed"
            await self._event_log.append(
                new_event(
                    event_type, "action", action.id,
                    output=result.output, error=result.error,
                    correlation_id="run_mcp", causation_id=proposed.id,
                )
            )
        if result.status.value != "succeeded":
            raise RuntimeError(f"MCP 工具执行失败: {result.error}")
        return result.output

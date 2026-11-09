"""spec §5.3 "仅定义不实现" 的预留契约（V1 无行为，导入即可）。"""

from __future__ import annotations

from typing import Any, Protocol


class ProtocolAdapter(Protocol):
    """外部协议（MCP/A2A/自定义）与内核对象的映射。"""

    protocol_name: str

    def import_capabilities(self) -> list[Any]: ...

    def import_event(self, payload: dict) -> Any: ...


class CompositionPolicy(Protocol):
    """多执行实体如何组织与调度（V1 仅单 Agent 循环）。"""

    def select_participants(self, task: Any, agents: list[Any]) -> list[str]: ...

    def decide_termination(self, task: Any, context: dict) -> str: ...


class AgentTransport(Protocol):
    """Agent 间通信与自定义协议桥。"""

    async def send(self, envelope: dict) -> None: ...

    async def receive(self) -> dict: ...


class EnvironmentAdapter(Protocol):
    """观察-行动-验证的环境接入（CUA 等场景 V1 不实现）。"""

    async def observe(self) -> dict: ...

    async def act(self, action: dict) -> dict: ...

    def supports_snapshot(self) -> bool: ...

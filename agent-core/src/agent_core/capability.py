"""CapabilityDescriptor / CapabilityRegistry — 统一能力目录（spec §5.3）。

tool / skill / model / retriever 收敛为同一描述结构；能力是声明出来的，
不是从类型标签推断的。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel


class CapabilityType(StrEnum):
    TOOL = "tool"
    SKILL = "skill"
    MODEL = "model"
    RETRIEVER = "retriever"


SideEffectLevel = Literal["none", "read", "write", "irreversible"]


class CapabilityDescriptor(BaseModel):
    id: str
    type: CapabilityType
    name: str
    input_schema: dict = {}
    output_schema: dict = {}
    side_effect_level: SideEffectLevel = "none"
    auth_scope: list[str] = []
    version: str = "0.1.0"
    tags: list[str] = []


class CapabilityRegistry(Protocol):
    def register(self, descriptor: CapabilityDescriptor) -> None: ...

    def discover(self, query: dict) -> list[CapabilityDescriptor]: ...

    def resolve(self, capability_id: str) -> CapabilityDescriptor: ...


class InMemoryCapabilityRegistry:
    """进程内实现；discover 按 type/tags 精确过滤，空查询返回全部。"""

    def __init__(self) -> None:
        self._by_id: dict[str, CapabilityDescriptor] = {}
        self._bindings: dict[str, object] = {}

    def bind(self, descriptor: CapabilityDescriptor, handler: object) -> None:
        """描述符与可调用实现的绑定（CapabilityExecutor 消费）。"""
        self.register(descriptor)
        self._bindings[descriptor.id] = handler

    def handler_for(self, capability_id: str):
        return self._bindings.get(capability_id)

    def register(self, descriptor: CapabilityDescriptor) -> None:
        self._by_id[descriptor.id] = descriptor

    def discover(self, query: dict) -> list[CapabilityDescriptor]:
        results = []
        for d in self._by_id.values():
            if "type" in query and d.type is not query["type"]:
                continue
            if "tags" in query and not set(query["tags"]) & set(d.tags):
                continue
            results.append(d)
        return results

    def resolve(self, capability_id: str) -> CapabilityDescriptor:
        return self._by_id[capability_id]

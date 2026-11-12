"""CUA（计算机使用）——HTTP EnvironmentAdapter 实现（V5 T8-T9）。

实现 reserved.py 的 EnvironmentAdapter Protocol：
- HTTPEnvironmentAdapter：observe → GET /observe（DOM+截图 URL）；act → POST /act
- register_cua_tools：把 observe/click/type/screenshot 包装为 CapabilityDescriptor
  注册进能力目录（模型可选用）。写操作如实声明 write 副作用（红线）。
"""

from __future__ import annotations

from typing import Any

import aiohttp

from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)


class HTTPEnvironmentAdapter:
    """实现 EnvironmentAdapter Protocol：HTTP 级 observe/act（非真浏览器）。"""

    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def observe(self) -> dict:
        """GET /observe → {dom, screenshot_url}。"""
        async with aiohttp.ClientSession(timeout=self._timeout) as client:
            async with client.get(f"{self._base_url}/observe") as resp:
                resp.raise_for_status()
                return await resp.json()

    async def act(self, action: dict) -> dict:
        """POST /act → {click, type, scroll, screenshot} 的执行结果。"""
        async with aiohttp.ClientSession(timeout=self._timeout) as client:
            async with client.post(f"{self._base_url}/act", json=action) as resp:
                resp.raise_for_status()
                return await resp.json()

    def supports_snapshot(self) -> bool:
        return True  # HTTP 桩环境始终可提供截图 URL


def register_cua_tools(
    registry: InMemoryCapabilityRegistry,
    adapter: Any,
) -> list[CapabilityDescriptor]:
    """把 CUA observe/act 包装为能力注册进目录（模型可选用）。

    副作用声明（红线）：observe/screenshot=read，click/type=write。
    """
    descriptors = [
        CapabilityDescriptor(
            id="cua_observe", type=CapabilityType.TOOL, name="env_observe",
            input_schema={}, side_effect_level="read", auth_scope=[],
            tags=["cua"],
        ),
        CapabilityDescriptor(
            id="cua_click", type=CapabilityType.TOOL, name="env_click",
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            side_effect_level="write", auth_scope=[], tags=["cua"],
        ),
        CapabilityDescriptor(
            id="cua_type", type=CapabilityType.TOOL, name="env_type",
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["target", "text"],
            },
            side_effect_level="write", auth_scope=[], tags=["cua"],
        ),
        CapabilityDescriptor(
            id="cua_screenshot", type=CapabilityType.TOOL, name="env_screenshot",
            input_schema={}, side_effect_level="read", auth_scope=[],
            tags=["cua"],
        ),
    ]

    async def _observe_handler(args: dict) -> dict:
        return await adapter.observe()

    async def _click_handler(args: dict) -> dict:
        return await adapter.act({"action": "click", "target": args["target"]})

    async def _type_handler(args: dict) -> dict:
        return await adapter.act({
            "action": "type", "target": args["target"], "text": args["text"],
        })

    async def _screenshot_handler(args: dict) -> dict:
        return await adapter.act({"action": "screenshot"})

    handlers = [
        _observe_handler, _click_handler, _type_handler, _screenshot_handler,
    ]
    for desc, handler in zip(descriptors, handlers, strict=True):
        registry.bind(desc, handler)
    return descriptors

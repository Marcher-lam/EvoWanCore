"""SkillRegistryBridge — 发布技能注册为沙箱 capability（V3 §5.2，RF#3 钉点）。

注册的 handler 一律走 SkillSandbox 子进程执行（网络 deny 默认）——
发布只登记，派发才执行，禁止 in-process exec。
"""

from __future__ import annotations

import json

from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityType,
)

from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox


class SkillRegistryBridge:
    def __init__(self, *, sandbox: SkillSandbox, registry: CapabilityRegistry) -> None:
        self._sandbox = sandbox
        self._registry = registry
        self._history: dict[str, list[SkillArtifact]] = {}  # id → 已发布版本序（回滚依据）

    def register(self, artifact: SkillArtifact) -> CapabilityDescriptor:
        """发布登记：版本化描述符 + 沙箱 handler 绑定（同 id 再发布即版本递增覆盖）。"""
        descriptor = CapabilityDescriptor(
            id=artifact.skill_id,
            type=CapabilityType.TOOL,
            name=artifact.skill_id,
            input_schema={"doc_id": "str", "dpi": "int"},
            output_schema={},
            side_effect_level="write",
            auth_scope=[],
            version=artifact.version,
            tags=["skill", "evolved"],
        )

        def _handler(args: dict, _art: SkillArtifact = artifact) -> object:
            async def _run() -> dict:
                result = await self._sandbox.run(_art, dict(args))
                if result.status != "succeeded":
                    raise RuntimeError(
                        f"技能 {_art.skill_id}@{_art.version} 执行失败: "
                        f"{result.error or result.stderr[:200]}"
                    )
                # 技能 stdout 约定：单行 JSON 对象（工件契约）
                try:
                    return json.loads(result.stdout.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    # I1 安全：不回传原始 stdout（最多 1MB 任意内容会流入模型上下文）
                    return {"error": "skill stdout is not valid JSON"}

            return _run()

        # 首次 bind、后续 rebind（版本覆盖）——沙箱技能不与静态工具抢 id
        try:
            self._registry.rebind(descriptor, _handler)
        except KeyError:
            self._registry.bind(descriptor, _handler)
        self._history.setdefault(artifact.skill_id, []).append(artifact)
        return descriptor

    def rollback(self, incumbent: SkillArtifact) -> None:
        """RF#4：回滚到指定前任版本（重新注册其行为）。"""
        self.register(incumbent)

    def unregister(self) -> None:
        """无更早版本时的回滚语义：技能整体下架（历史清空）。"""
        for versions in self._history.values():
            for art in versions:
                self._registry._by_id.pop(art.skill_id, None)  # noqa: SLF001
                self._registry._bindings.pop(art.skill_id, None)  # noqa: SLF001
        self._history.clear()

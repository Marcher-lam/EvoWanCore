"""MemoryRetriever / ContextAssembler — 确定性召回与上下文组装（spec §5.4）。

V1 无向量检索：分数 = 验证等级基础分 + 关键词命中加分；环境版本不匹配 →
直接排除。"存了什么"与"本轮给模型看什么"是两个接口。
"""

from __future__ import annotations

from typing import Protocol

from agent_core.memory.record import LifecycleState, MemoryRecord, VerificationState
from agent_core.memory.store import MemoryStore

_BASE_SCORE = {
    VerificationState.VERIFIED: 10.0,
    VerificationState.DISPUTED: 2.0,
    VerificationState.CANDIDATE: 1.0,
    VerificationState.REJECTED: 0.0,
}


class MemoryRetriever(Protocol):
    async def retrieve(
        self, scope: dict, task_context: dict, limit: int
    ) -> list[MemoryRecord]: ...


class SimpleRetriever:
    """确定性打分：VERIFIED/ACTIVE 强加权；environment_version 不匹配排除。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def retrieve(self, scope: dict, task_context: dict, limit: int) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        for ns in scope.get("namespaces", []):
            records.extend(await self._store.query(ns, scope.get("filter", {})))

        env = task_context.get("environment_version")
        keywords = [k.lower() for k in task_context.get("keywords", [])]

        # I3: 只有 ACTIVE 生命周期才可进入执行上下文（回滚/失效必须立刻生效）
        records = [r for r in records if r.lifecycle is LifecycleState.ACTIVE]

        scored: list[tuple[float, int, MemoryRecord]] = []
        for idx, r in enumerate(records):
            if env is not None and r.environment_version is not None:
                if r.environment_version != env:
                    continue  # 环境版本不匹配：降权为 0，直接排除
            score = _BASE_SCORE.get(r.verification, 0.0)
            text = str(r.content).lower()
            score += 5.0 * sum(1 for k in keywords if k in text)
            scored.append((score, idx, r))

        scored.sort(key=lambda t: (-t[0], t[1]))
        return [r for _, _, r in scored[:limit]]


class ContextAssembler(Protocol):
    def assemble(self, records: list[MemoryRecord], token_budget: int) -> str: ...


class TokenBudgetAssembler:
    """按传入顺序装填；超预算截断并标注。"""

    @staticmethod
    def _render(record: MemoryRecord) -> str:
        statement = record.content.get("statement", str(record.content))
        return f"- [{record.kind.value}|{record.verification.value}] {statement}"

    def assemble(self, records: list[MemoryRecord], token_budget: int) -> str:
        lines: list[str] = []
        used = 0
        truncated = False
        for r in records:
            line = self._render(r)
            cost = max(1, len(line) // 4)  # 粗估 token
            if used + cost > token_budget:
                truncated = True
                break
            lines.append(line)
            used += cost
        text = "\n".join(lines)
        if truncated:
            text += "\n[memory truncated]"
        return text

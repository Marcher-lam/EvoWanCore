"""MRAGApp——多模态 RAG 一条链组装（V11-T5）。

ingest(SourceDoc) → retrieve(query) → answer(query) → register_into(能力目录)。
真实部署：vlm 换 lmstudio_vlm 适配器、embedder 换 LmStudioEmbedding——
接口与 Fake 全家桶一致（零网络测试同链路）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from examples.mrag.answer import Answer, VLMAnswerer
from examples.mrag.embed import HashEmbedding
from examples.mrag.ingest import PageDescriber, ingest
from examples.mrag.store import MRAGStore, RetrievedChunk, SourceDoc


class MRAGApp:
    def __init__(
        self,
        *,
        db_path: Path | str,
        blobs_dir: Path | str,
        embedder: HashEmbedding | None = None,
        vlm: Any,
    ) -> None:
        self.store = MRAGStore(db_path, blobs_dir, embedder or HashEmbedding())
        self.vlm = vlm
        self.describer = PageDescriber(vlm)
        self.answerer = VLMAnswerer(vlm)

    async def ingest(self, doc: SourceDoc) -> int:
        return await ingest(self.store, doc, self.describer)

    async def retrieve(self, query: str, k: int = 4) -> list[RetrievedChunk]:
        return await self.store.retrieve(query, k=k)

    async def answer(self, query: str, k: int = 4) -> Answer:
        return await self.answerer.answer(query, await self.retrieve(query, k))

    def register_into(self, registry: Any) -> None:
        """接入能力目录——AgentRuntime 即可调用 mrag_query/mrag_ingest。"""
        from examples.mrag.capabilities import register_mrag_tools

        register_mrag_tools(registry, self.store, self.answerer)

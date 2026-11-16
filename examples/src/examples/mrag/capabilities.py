"""MRAG 能力接入——mrag_query / mrag_ingest 注册进能力目录（V11-T4）。

"基于本项目"的关键：MRAG 不是孤立脚本，AgentRuntime 可经能力目录调用。
副作用如实声明红线：query=read（只读索引）；ingest=write（改索引）。
"""

from __future__ import annotations

from typing import Any

from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)

from examples.mrag.answer import VLMAnswerer
from examples.mrag.store import MRAGStore


def register_mrag_tools(
    registry: InMemoryCapabilityRegistry,
    store: MRAGStore,
    answerer: VLMAnswerer,
) -> list[CapabilityDescriptor]:
    descriptors = [
        CapabilityDescriptor(
            id="mrag_query", type=CapabilityType.TOOL, name="rag_query",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "default": 4},
                },
                "required": ["query"],
            },
            side_effect_level="read", auth_scope=[], tags=["mrag"],
        ),
        CapabilityDescriptor(
            id="mrag_ingest", type=CapabilityType.TOOL, name="rag_ingest",
            input_schema={
                "type": "object",
                "properties": {
                    "doc_name": {"type": "string"},
                    "page_image_paths": {"type": "array",
                                         "items": {"type": "string"}},
                    "text": {"type": "string"},
                },
                "required": ["doc_name", "page_image_paths"],
            },
            side_effect_level="write", auth_scope=[], tags=["mrag"],
        ),
    ]

    async def _query_handler(args: dict) -> dict:
        retrieved = await store.retrieve(args["query"], k=args.get("k", 4))
        return {"hits": [{
            "content": c.content, "doc": c.doc, "page": c.page,
            "score": round(c.score, 4), "page_image_path": c.page_image_path,
        } for c in retrieved]}

    async def _ingest_handler(args: dict) -> dict:
        from examples.mrag.ingest import PageDescriber
        from examples.mrag.store import SourceDoc

        describer: Any = PageDescriber(answerer._vlm)  # noqa: SLF001 — 同一 VLM
        pages = [(i + 1, Path(p).read_bytes())
                 for i, p in enumerate(args["page_image_paths"])]
        doc = SourceDoc(name=args["doc_name"], pages=pages,
                        text=args.get("text", ""))
        from examples.mrag.ingest import ingest

        n = await ingest(store, doc, describer)
        return {"indexed_chunks": n}

    from pathlib import Path

    registry.bind(descriptors[0], _query_handler)
    registry.bind(descriptors[1], _ingest_handler)
    return descriptors

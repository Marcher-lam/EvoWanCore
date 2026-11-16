"""MRAG 能力接入——mrag_query / mrag_ingest 注册进能力目录（V11-T4）。

"基于本项目"的关键：MRAG 不是孤立脚本，AgentRuntime 可经能力目录调用。
副作用如实声明红线：query=read（只读索引）；ingest=write（改索引）。
ingest 的 page_image_paths 受限在 blobs_dir 内（防 LLM 传任意主机路径
外泄文件——终评审 H1）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)

from examples.mrag.answer import VLMAnswerer
from examples.mrag.ingest import PageDescriber
from examples.mrag.store import MRAGStore


def _resolve_within(path: str, root: Path) -> Path:
    """路径必须落在 root 内（resolve 后前缀校验）——拒绝绝对/穿越越界。"""
    resolved = (root / path).resolve() if not Path(path).is_absolute() \
        else Path(path).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"路径越界（只允许 {root} 内）: {path}")
    if not resolved.exists():
        raise FileNotFoundError(f"页面图像不存在: {path}")
    return resolved


def register_mrag_tools(
    registry: InMemoryCapabilityRegistry,
    store: MRAGStore,
    answerer: VLMAnswerer,
    vlm: Any,
) -> list[CapabilityDescriptor]:
    """vlm 显式传入（ingest 描述器与 answer 共用同一适配器实例）。"""
    descriptors = [
        CapabilityDescriptor(
            id="mrag_query", type=CapabilityType.TOOL, name="mrag_query",
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
            id="mrag_ingest", type=CapabilityType.TOOL, name="mrag_ingest",
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
            "score": round(c.score, 4),
            "page_image": Path(c.page_image_path).name,  # 文件名，不回传宿主绝对路径
        } for c in retrieved]}

    async def _ingest_handler(args: dict) -> dict:
        from examples.mrag.ingest import ingest
        from examples.mrag.store import SourceDoc

        paths = args["page_image_paths"]
        text = args.get("text", "")
        if not paths and not text:
            raise ValueError("page_image_paths 与 text 至少其一非空")
        pages: list[tuple[int, bytes]] = []
        for i, p in enumerate(paths):  # 每路径 resolve+前缀校验+存在校验
            resolved = _resolve_within(p, store.blobs_dir)
            pages.append((i + 1, resolved.read_bytes()))
        doc = SourceDoc(name=args["doc_name"], pages=pages, text=text)
        n = await ingest(store, doc, PageDescriber(vlm))
        return {"indexed_chunks": n}

    registry.bind(descriptors[0], _query_handler)
    registry.bind(descriptors[1], _ingest_handler)
    return descriptors

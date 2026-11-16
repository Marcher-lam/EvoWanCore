"""MRAG answer——检索结果（文本块+页面图像）→ VLM 带引用答案（V11-T4）。"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from examples.mrag.store import RetrievedChunk

_ANSWER_PROMPT = (
    "你是多模态文档问答助手。基于下列检索到的文本块与页面图像回答问题。\n"
    "要求：1) 只用给定材料，不要编造；2) 每个事实后附引用标记 [文档名 p.页码]；"
    "3) 若材料不足以回答，明说'材料中未包含'。\n\n"
)


@dataclass
class Answer:
    text: str
    citations: list[tuple[str, int]]  # [(doc, page)] 去重保序


def parse_citations(text: str) -> list[tuple[str, int]]:
    """从答案文本解析 [doc p.N] 引用（去重保序）。"""
    out: list[tuple[str, int]] = []
    for m in re.finditer(r"\[([^\]\s]+)\s+p\.(\d+)\]", text):
        item = (m.group(1), int(m.group(2)))
        if item not in out:
            out.append(item)
    return out


class VLMAnswerer:
    def __init__(self, vlm: Any) -> None:
        self._vlm = vlm

    async def answer(self, query: str, retrieved: list[RetrievedChunk]) -> Answer:
        if not retrieved:
            return Answer(text="材料中未包含相关信息。", citations=[])
        parts: list[str] = [_ANSWER_PROMPT, f"问题：{query}\n\n检索块："]
        for i, chunk in enumerate(retrieved, 1):
            parts.append(
                f"[{i}] ({chunk.doc} p.{chunk.page}, 相关度 {chunk.score:.3f}) "
                f"{chunk.content}"
            )
        # 多模态证据：首命中的页面图像附给 VLM（视觉上下文）
        first_img = next(
            (c.page_image_path for c in retrieved if c.page_image_path), None
        )
        if first_img and Path(first_img).exists():
            b64 = base64.b64encode(Path(first_img).read_bytes()).decode()
            parts.append(f"\n首命中页面图像：\n![page](data:image/png;base64,{b64})")
        parts.append(f"\n请回答：{query}")

        from agent_core.models import InferRequest

        resp = await self._vlm.infer(InferRequest(
            model_id=self._vlm.capabilities().model_id,
            messages=[{"role": "user", "content": "\n".join(parts)}],
        ))
        text = resp.text or ""
        return Answer(text=text, citations=parse_citations(text))

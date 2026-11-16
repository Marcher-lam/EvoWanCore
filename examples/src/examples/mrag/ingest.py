"""MRAG ingest——VLM 逐页描述 → 描述块入库（V11-T3）。

PageDescriber：页面 PNG → data URL 注入提示词 → VLM 完整描述
（转录文本 + 表格结构 + 图形要点）。与 docworkbench 的 VLM 调用同模式；
FakeVLM 可零网络测试。
"""

from __future__ import annotations

import base64
from typing import Any

from examples.mrag.store import MRAGStore, SourceDoc

_DESCRIBE_PROMPT = (
    "你是文档页面分析器。请完整描述这页图像：1) 转录全部可见文本"
    "（保持原语言，不要翻译）；2) 若有表格，用简明结构描述行列含义；"
    "3) 若有图表/图形，说明类型与关键数值。输出一段紧凑描述，不要寒暄。"
)


class PageDescriber:
    def __init__(self, vlm: Any) -> None:
        self._vlm = vlm

    async def describe(self, png: bytes) -> str:
        data_url = (
            "data:image/png;base64," + base64.b64encode(png).decode()
        )
        request = {
            "model_id": self._vlm.capabilities().model_id,
            "messages": [{
                "role": "user",
                "content": f"{_DESCRIBE_PROMPT}\n![page]({data_url})",
            }],
        }
        from agent_core.models import InferRequest

        resp = await self._vlm.infer(InferRequest(**request))
        return resp.text or ""


async def ingest(store: MRAGStore, doc: SourceDoc, describer: PageDescriber) -> int:
    """逐页 VLM 描述 → 与全文块一起入库。返回入库块数。"""
    descriptions: list[str] = []
    for _page_no, png in doc.pages:
        descriptions.append(await describer.describe(png))
    return await store.index(doc, page_descriptions=descriptions)

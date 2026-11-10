"""WorkbenchFakeVLM — 对比度条件读图（V2 §5.2，第四隐藏约束的可测载体）。

字段值只在渲染 PNG 与真值表；本 FakeVLM 模拟"读图"：按**当前页对比度**决定
读出质量——全页 >=0.5 → 真值；任一页 <0.5 → 字段确定性错值（hash 扰动）。
convert_format(dpi>=150) 把页对比度提到 1.0 后读全对——约束可学习也可解除。
真 VLM 即插即跑（key 后补），接口契约不变。
"""

from __future__ import annotations

import hashlib
import json
import re

from agent_core.models import InferRequest, InferResponse, Modality, ModelCapabilities

_DOC_ID = re.compile(r"doc_\d+")


def _perturb(doc_id: str, name: str, value: object) -> object:
    """确定性错值（同输入同输出，且保证 ≠ 真值）。"""
    salt = 0
    while True:
        digest = hashlib.sha256(f"{doc_id}:{name}:{value}:{salt}".encode()).hexdigest()
        if isinstance(value, int):
            candidate = int(digest[:6], 16) % 90000 + 1000
        else:
            candidate = f"{value}~{digest[:4]}"
        if candidate != value:
            return candidate
        salt += 1


class WorkbenchFakeVLM:
    """ModelAdapter 实现：读图抽取以 submit_extracted_fields 工具调用交付。"""

    def __init__(self, corpus, derived: dict) -> None:
        self._corpus = corpus
        self._derived = derived
        self.recorded_requests: list[InferRequest] = []

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            model_id="fake-vlm",
            inputs={Modality.TEXT, Modality.IMAGE},
            outputs={Modality.TEXT},
            tools={"native_tool_call"},
        )

    async def infer(self, request: InferRequest) -> InferResponse:
        self.recorded_requests.append(request)
        blob = " ".join(
            m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
            for m in request.messages
        )
        match = _DOC_ID.search(blob)
        if not match:
            return InferResponse(text="no document reference in request")
        doc_id = match.group(0)
        doc = self._derived.get(doc_id) or next(
            (d for d in self._corpus.documents if d["id"] == doc_id), None
        )
        if doc is None:
            return InferResponse(text=f"unknown document {doc_id}")
        return InferResponse(
            tool_calls=[
                {
                    "id": "vlm_submit_1",
                    "name": "submit_extracted_fields",
                    "arguments": {"doc_id": doc_id, "fields": self._read_fields(doc)},
                }
            ]
        )

    def _read_fields(self, doc: dict) -> dict:
        """对比度条件读图：任一页 <0.5 → 全字段确定性错值（hash 扰动）。"""
        low_contrast = any(float(p.get("contrast", 1.0)) < 0.5 for p in doc["pages"])
        return {
            f["name"]: (
                _perturb(doc["id"], f["name"], f["value"]) if low_contrast else f["value"]
            )
            for f in doc["fields"]
        }

"""V11-T5 MRAG e2e：ingest → retrieve → answer 全链（零网络 Fake 全家桶）。"""

import re
from pathlib import Path

from examples.mrag.embed import HashEmbedding


def _png(n: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + f"page-{n}".encode()


class _EchoVLM:
    """按页面序号返回描述；answer 阶段复述首块内容并附引用。"""

    def __init__(self) -> None:
        from agent_core.models import Modality, ModelCapabilities

        self._caps = ModelCapabilities(
            model_id="m-e2e", inputs={Modality.TEXT, Modality.IMAGE},
        )
        self.calls = 0

    def capabilities(self):
        return self._caps

    async def infer(self, request):
        from agent_core.models import InferResponse

        self.calls += 1
        prompt = str(request.messages)
        if "文档问答助手" in prompt:  # answer 阶段
            m = re.search(r"\[1\] \((\S+) p\.(\d+)", prompt)
            doc, page = (m.group(1), int(m.group(2))) if m else ("?", 1)
            fact = re.search(r"相关度 [\d.]+\)\s*([^\n]+)", prompt)
            return InferResponse(
                text=f"{fact.group(1) if fact else '见材料'} [{doc} p.{page}]。"
            )
        # 描述阶段：按调用序给页码描述
        return InferResponse(text=f"第 {self.calls} 页描述：单据版式页面")


async def test_mrag_full_chain(tmp_path: Path) -> None:
    """3 页图文 mini corpus：ingest(VLM×3) → retrieve 命中 → 答案带正确引用。"""
    from examples.mrag.app import MRAGApp
    from examples.mrag.store import SourceDoc

    app = MRAGApp(
        db_path=tmp_path / "mrag.db",
        blobs_dir=tmp_path / "blobs",
        embedder=HashEmbedding(dim=64),
        vlm=_EchoVLM(),
    )
    doc = SourceDoc(
        name="expense.pdf",
        pages=[(i, _png(i)) for i in range(1, 4)],
        text=(
            "第1页：报销单封面，申请人张三。\n"
            "第2页：出租车发票 120 元，日期 2026-10-30。\n"
            "第3页：审批意见：同意报销，签字李四。"
        ),
    )
    n = await app.ingest(doc)
    assert n >= 6, f"3 页描述 + 3 文本块，实际 {n}"
    assert app.vlm.calls == 3, "每页一次 VLM 描述"

    # 检索：问出租车费用 → 应命中第 2 页文本块
    hits = await app.retrieve("出租车发票多少钱", k=2)
    assert "120" in hits[0].content or "出租车" in hits[0].content

    # 生成：答案必须引用正确文档页
    answer = await app.answer("打车花了多少钱？")
    assert "expense.pdf" in answer.text
    assert ("expense.pdf", 2) in answer.citations or \
        ("expense.pdf", hits[0].page) in answer.citations, \
        f"引用页错误: {answer.citations}"


async def test_mrag_app_registers_capabilities(tmp_path: Path) -> None:
    """MRAGApp 一行接入能力目录（基于本项目的落点）。"""
    from agent_core.capability import InMemoryCapabilityRegistry
    from examples.mrag.app import MRAGApp

    app = MRAGApp(
        db_path=tmp_path / "c.db", blobs_dir=tmp_path / "cb",
        embedder=HashEmbedding(), vlm=_EchoVLM(),
    )
    registry = InMemoryCapabilityRegistry()
    app.register_into(registry)
    ids = {d.id for d in registry.discover({"tags": ["mrag"]})}
    assert {"mrag_query", "mrag_ingest"} <= ids

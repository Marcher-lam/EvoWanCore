"""V11-T3 ingest：VLM 逐页描述 → 描述块入库（FakeVLM 零网络）。"""

from pathlib import Path

from examples.mrag.embed import HashEmbedding


class _FakeVLM:
    """脚本化 VLM：记录请求，对图像请求返回固定描述。"""

    def __init__(self) -> None:
        from agent_core.models import Modality, ModelCapabilities

        self._caps = ModelCapabilities(
            model_id="m-fake-vlm",
            inputs={Modality.TEXT, Modality.IMAGE},
        )
        self.requests: list = []

    def capabilities(self):
        return self._caps

    async def infer(self, request):
        from agent_core.models import InferResponse

        self.requests.append(request)
        text = (request.messages[0].get("content") if request.messages else "")
        has_image = "data:image" in str(text)
        desc = f"[FAKE-DESC {'含图' if has_image else '纯文'} 页面 {len(self.requests)}]"
        return InferResponse(text=desc)


def _png(n: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + f"p{n}".encode()


async def test_describer_prompt_contains_image(tmp_path: Path) -> None:
    """PageDescriber 提示词必须带页面图像 data URL（多模态输入真到位）。"""
    from examples.mrag.ingest import PageDescriber

    vlm = _FakeVLM()
    d = PageDescriber(vlm)
    desc = await d.describe(_png(1))
    assert "FAKE-DESC 含图" in desc, f"VLM 未收到图像: {desc}"
    req = vlm.requests[0]
    content = req.messages[0]["content"]
    assert isinstance(content, list), f"content 必须是 parts 列表（OpenAI 契约）: {type(content)}"
    parts = {p["type"] for p in content}
    assert "text" in parts and "image_url" in parts
    img = next(p for p in content if p["type"] == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,"), \
        "图像必须走 image_url content part（真实 VLM 不解析 markdown 内嵌）"


async def test_ingest_end_to_end_chunks(tmp_path: Path) -> None:
    """ingest：逐页描述 → store 同时入 text 块与 page_desc 块。"""
    from examples.mrag.ingest import PageDescriber, ingest
    from examples.mrag.store import MRAGStore, SourceDoc

    vlm = _FakeVLM()
    store = MRAGStore(tmp_path / "m.db", tmp_path / "b", HashEmbedding())
    doc = SourceDoc(
        name="r.pdf",
        pages=[(1, _png(1)), (2, _png(2))],
        text="第1页：金额 100 元。",
    )
    n = await ingest(store, doc, PageDescriber(vlm))
    assert n >= 3, f"2 页描述 + ≥1 文本块，实际 {n}"
    assert len(vlm.requests) == 2, "每页必须各调一次 VLM"
    hits = await store.retrieve("[FAKE-DESC 含图 页面 2]", k=1)
    assert "页面 2" in hits[0].content, "页描述块必须可被检索"

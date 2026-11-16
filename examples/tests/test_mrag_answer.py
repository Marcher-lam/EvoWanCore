"""V11-T4 answer（VLM 带引用生成）+ capabilities（能力目录接入）。"""

from pathlib import Path

from examples.mrag.embed import HashEmbedding
from examples.mrag.store import MRAGStore, SourceDoc


class _ScriptedVLM:
    """按 prompt 内容路由回复的假 VLM。"""

    def __init__(self, reply_fn):
        from agent_core.models import Modality, ModelCapabilities

        self._caps = ModelCapabilities(
            model_id="m-ans", inputs={Modality.TEXT, Modality.IMAGE},
        )
        self._reply_fn = reply_fn
        self.requests: list = []

    def capabilities(self):
        return self._caps

    async def infer(self, request):
        from agent_core.models import InferResponse

        self.requests.append(request)
        prompt = str(request.messages)
        return InferResponse(text=self._reply_fn(prompt))


def _png(n: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + f"p{n}".encode()


async def _seed_store(tmp_path: Path) -> MRAGStore:
    store = MRAGStore(tmp_path / "m.db", tmp_path / "b", HashEmbedding())
    await store.index(
        SourceDoc(
            name="inv.pdf", pages=[(1, _png(1)), (2, _png(2))],
            text="第1页：总金额 1234.56 元。第2页：房费 800 元。",
        ),
        page_descriptions=["发票页", "酒店页"],
    )
    return store


async def test_answerer_prompt_and_citations(tmp_path: Path) -> None:
    """prompt 含检索块 + 首命中页面图像；答案引用 [doc p.N] 被解析。"""

    def reply(prompt: str) -> str:
        assert "1234.56" in prompt, "prompt 必须含检索块文本"
        assert "data:image/png;base64," in prompt, "prompt 必须含页面图像"
        return "发票总金额为 1234.56 元 [inv.pdf p.1]。"

    from examples.mrag.answer import VLMAnswerer

    store = await _seed_store(tmp_path)
    vlm = _ScriptedVLM(reply)
    answerer = VLMAnswerer(vlm)
    retrieved = await store.retrieve("总金额", k=2)
    answer = await answerer.answer("发票总金额多少？", retrieved)

    assert "1234.56" in answer.text
    assert answer.citations == [("inv.pdf", 1)], f"引用解析错误: {answer.citations}"


async def test_capabilities_registered_with_redlines(tmp_path: Path) -> None:
    """mrag_query=read / mrag_ingest=write——副作用如实声明红线。"""
    from agent_core.capability import InMemoryCapabilityRegistry
    from examples.mrag.answer import VLMAnswerer
    from examples.mrag.capabilities import register_mrag_tools

    store = await _seed_store(tmp_path)
    answerer = VLMAnswerer(_ScriptedVLM(lambda p: "x"))

    registry = InMemoryCapabilityRegistry()
    vlm = _ScriptedVLM(lambda p: "x")
    register_mrag_tools(registry, store, answerer, vlm)

    by_id = {d.id: d for d in registry.discover({})}
    assert by_id["mrag_query"].side_effect_level == "read"
    assert by_id["mrag_query"].auth_scope == []
    assert by_id["mrag_ingest"].side_effect_level == "write", \
        "ingest 改索引——必须如实声明 write"
    # handler 真通：query → retrieve JSON（公共 handler_for，不触私有）
    handler = registry.handler_for("mrag_query")
    result = await handler({"query": "总金额", "k": 2})
    assert len(result["hits"]) == 2
    assert all("content" in h and "page" in h for h in result["hits"])


# ---------- V11 终评审追加：ingest handler 安全面 + 短查询 ----------

async def test_ingest_handler_path_traversal_rejected(tmp_path: Path) -> None:
    """页面路径越界（绝对/穿越）与缺失 → 显式错误，绝不读宿主文件。"""
    import pytest
    from agent_core.capability import InMemoryCapabilityRegistry
    from examples.mrag.answer import VLMAnswerer
    from examples.mrag.capabilities import register_mrag_tools

    store = await _seed_store(tmp_path)
    registry = InMemoryCapabilityRegistry()
    register_mrag_tools(
        registry, store, VLMAnswerer(_ScriptedVLM(lambda p: "d")), 
        _ScriptedVLM(lambda p: "d"),
    )
    ingest_h = registry.handler_for("mrag_ingest")
    # 绝对路径指向库外（模拟 ~/.ssh/id_rsa 外泄通道）
    with pytest.raises(ValueError, match="越界"):
        await ingest_h({"doc_name": "evil",
                        "page_image_paths": ["/etc/hosts"]})
    with pytest.raises(ValueError, match="越界"):
        await ingest_h({"doc_name": "evil",
                        "page_image_paths": ["../../../../etc/hosts"]})
    with pytest.raises(FileNotFoundError):
        await ingest_h({"doc_name": "x", "page_image_paths": ["ghost.png"]})
    with pytest.raises(ValueError, match="至少其一非空"):
        await ingest_h({"doc_name": "x", "page_image_paths": []})


async def test_ingest_handler_within_blobs_ok(tmp_path: Path) -> None:
    """blobs_dir 内的相对路径正常入库（合法路径不被守卫误伤）。"""
    from agent_core.capability import InMemoryCapabilityRegistry
    from examples.mrag.answer import VLMAnswerer
    from examples.mrag.capabilities import register_mrag_tools

    store = await _seed_store(tmp_path)
    # 先落一张合法页面图进 blobs_dir
    (tmp_path / "b").mkdir(exist_ok=True)
    (tmp_path / "b" / "extra_p1.png").write_bytes(_png(9))
    registry = InMemoryCapabilityRegistry()
    vlm = _ScriptedVLM(lambda p: "补充页描述")
    register_mrag_tools(registry, store, VLMAnswerer(vlm), vlm)
    result = await registry.handler_for("mrag_ingest")({
        "doc_name": "extra.pdf",
        "page_image_paths": ["extra_p1.png"],
        "text": "第1页：补充材料。",
    })
    assert result["indexed_chunks"] >= 2


async def test_short_query_two_char_hits(tmp_path: Path) -> None:
    """二字中文查询（'房费'）非零分命中——1/2-gram 并集的验收。"""
    store = await _seed_store(tmp_path)
    hits = await store.retrieve("房费", k=3)
    assert hits and hits[0].score > 0.0, "二字查询不得全零分退化"
    assert "800" in hits[0].content or "房费" in hits[0].content

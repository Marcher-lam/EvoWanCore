"""V11-T2 MRAGStore：文本/页描述块索引 + 多模态检索（页面图像引用）。"""

from pathlib import Path

from examples.mrag.embed import HashEmbedding


def _png(n: int = 1) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + f"page-{n}".encode()


def _doc(tmp_path: Path, name: str = "invoice.pdf", pages: int = 2):
    from examples.mrag.store import SourceDoc

    return SourceDoc(
        name=name,
        pages=[(i, _png(i)) for i in range(1, pages + 1)],
        text=(
            "第1页：发票抬头 ACME 公司，总金额 1234.56 元，日期 2026-11-01。\n"
            "第2页：酒店入住 2026-11-02，退房 2026-11-04，房费 800 元。"
        ) if pages == 2 else "只有一页的文本",
    )


async def test_index_and_retrieve_top_k(tmp_path: Path) -> None:
    """检索命中正确块：查询'总金额'→发票金额块排首；带页面图像引用。"""
    from examples.mrag.store import MRAGStore

    store = MRAGStore(tmp_path / "mrag.db", tmp_path / "blobs", HashEmbedding())
    doc = _doc(tmp_path)
    n = await store.index(
        doc,
        page_descriptions=["发票版式页面，含金额表格", "酒店订单确认页"],
    )
    assert n >= 4, "文本块 + 页描述块都应入库"

    hits = await store.retrieve("发票总金额多少", k=2)
    assert len(hits) == 2
    top = hits[0]
    assert "1234.56" in top.content or "总金额" in top.content, \
        f"top-1 应命中金额块，实际: {top.content[:40]}"
    assert top.doc == "invoice.pdf"
    assert top.page >= 1
    # 多模态证据：命中块关联的页面图像必须真实存在
    assert Path(top.page_image_path).exists(), "页面图像引用必须落盘可用"


async def test_retrieve_scores_sorted(tmp_path: Path) -> None:
    """top-k 按相似度降序。"""
    from examples.mrag.store import MRAGStore

    store = MRAGStore(tmp_path / "mrag.db", tmp_path / "blobs", HashEmbedding())
    await store.index(_doc(tmp_path), page_descriptions=["页一", "页二"])
    hits = await store.retrieve("酒店退房时间", k=4)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), f"未降序: {scores}"


async def test_query_embedder_alignment(tmp_path: Path) -> None:
    """索引与查询共用同一 embedder 实例语义（跨实例同配置也可对齐）。"""
    from examples.mrag.store import MRAGStore

    store_a = MRAGStore(tmp_path / "a.db", tmp_path / "ba", HashEmbedding(dim=64))
    await store_a.index(_doc(tmp_path), page_descriptions=["p1", "p2"])
    store_b = MRAGStore(  # 新实例重开同一库
        tmp_path / "a.db", tmp_path / "ba", HashEmbedding(dim=64)
    )
    hits = await store_b.retrieve("酒店房费", k=2)
    assert len(hits) == 2, "重开索引库后检索仍可用（向量已持久化）"


async def test_empty_index_returns_empty(tmp_path: Path) -> None:
    from examples.mrag.store import MRAGStore

    store = MRAGStore(tmp_path / "e.db", tmp_path / "be", HashEmbedding())
    assert await store.retrieve("anything", k=3) == []

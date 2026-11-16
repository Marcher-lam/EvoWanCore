"""V11-T1 MRAG EmbeddingProvider：Hash 确定性 + LmStudio 真实路径协议。"""

import math


async def test_hash_embedding_deterministic() -> None:
    """同一文本 → 同一向量（跨实例稳定——索引与查询必须可对齐）。"""
    from examples.mrag.embed import HashEmbedding

    a = HashEmbedding(dim=64)
    b = HashEmbedding(dim=64)
    va = (await a.embed(["发票总金额 123 元"]))[0]
    vb = (await b.embed(["发票总金额 123 元"]))[0]
    assert va == vb


async def test_hash_embedding_discriminates() -> None:
    """不同文本 → 向量有余弦差（检索有意义的前提）。"""
    from examples.mrag.embed import HashEmbedding

    e = HashEmbedding(dim=64)
    v1, v2 = await e.embed(["发票总金额", "酒店入住日期"])
    cos = sum(a * b for a, b in zip(v1, v2, strict=True))
    assert cos < 0.999, f"不同文本向量几乎相同: cos={cos}"


async def test_hash_embedding_unit_norm_and_batch() -> None:
    """单位范数（余弦=点积）+ 批量保序。"""
    from examples.mrag.embed import HashEmbedding

    e = HashEmbedding(dim=32)
    vecs = await e.embed(["a", "b", "c"])
    assert len(vecs) == 3
    for v in vecs:
        assert len(v) == 32
        assert math.isclose(
            math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6
        ), "必须归一化（余弦=点积的前提）"


def test_lmstudio_embedding_protocol() -> None:
    """LmStudioEmbedding 走 /v1/embeddings（协议形状单测，不打网络）。"""
    from examples.mrag.embed import LmStudioEmbedding

    e = LmStudioEmbedding(
        base_url="http://127.0.0.1:1234/v1", model="text-embedding"
    )
    assert e.model == "text-embedding"
    assert e.url.endswith("/v1/embeddings")

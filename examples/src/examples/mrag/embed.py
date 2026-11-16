"""MRAG EmbeddingProvider——索引与查询共用的向量化（V11-T1）。

- HashEmbedding：SHA 分桶 → 单位向量。确定性、零依赖——测试与无网络兜底；
  检索质量有限但可复现（语义检索请接 LmStudioEmbedding）。
- LmStudioEmbedding：LM Studio /v1/embeddings（OpenAI 兼容）真实路径。
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import httpx


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedding:
    """确定性哈希向量：SHA-256 块分桶计数 → L2 归一化。

    同一文本跨实例恒同向量；不同文本以高概率区分（dim 越高越散）。
    """

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        # 滑窗 n-gram 分桶：短文本也有多桶信号（而非整句一桶）
        for i in range(len(text)):
            gram = text[i:i + 3]
            bucket = int.from_bytes(
                hashlib.sha256(gram.encode()).digest()[:8], "big"
            ) % self._dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0:  # 空文本：均匀向量（零信息但不炸检索）
            return [1.0 / math.sqrt(self._dim)] * self._dim
        return [x / norm for x in vec]


class LmStudioEmbedding:
    """LM Studio /v1/embeddings（OpenAI 兼容；与 lmstudio_vlm 同供应商惯例）。"""

    def __init__(self, base_url: str, model: str, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout_s

    @property
    def url(self) -> str:
        return f"{self._base_url}/embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self.url,
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
        # OpenAI 兼容：按 index 保序
        ordered = sorted(data, key=lambda d: d["index"])
        return [d["embedding"] for d in ordered]

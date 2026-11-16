"""MRAGStore——多模态 RAG 索引与检索（V11-T2，sqlite 惯例同内核）。

块模型：text 块（全文按句切段）+ page_desc 块（VLM 页面描述）。
多模态证据：每块携带所属页面图像的落盘路径（回答时可直接附图）。
检索：与查询向量点积（向量已归一化，点积=余弦）top-k。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from examples.mrag.embed import EmbeddingProvider, HashEmbedding

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc TEXT NOT NULL,
    page INTEGER NOT NULL,
    kind TEXT NOT NULL,             -- text | page_desc
    content TEXT NOT NULL,
    vec TEXT NOT NULL,              -- JSON 归一化向量
    page_image_path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class SourceDoc:
    """ingest 输入：页面图像 + 可选全文（PDF 解析是非目标——corpus 管道供图）。"""

    name: str
    pages: list[tuple[int, bytes]]  # [(page_no, png_bytes)]
    text: str = ""


@dataclass
class RetrievedChunk:
    content: str
    score: float
    doc: str
    page: int
    page_image_path: str


def _sentences(text: str, max_len: int = 200) -> list[str]:
    """按句/换行切段；超长段切续块（内容不丢弃——长表行是核心语料）。"""
    parts = re.split(r"(?<=[。．!？?\n])", text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for i in range(0, len(p), max_len):
            out.append(p[i:i + max_len])
    return out


class MRAGStore:
    def __init__(self, db_path: Path | str, blobs_dir: Path | str,
                 embedder: EmbeddingProvider | None = None) -> None:
        self.db_path = Path(db_path)
        self.blobs_dir = Path(blobs_dir)
        self._embedder = embedder or HashEmbedding()
        self._schema_ready = False

    async def _assert_meta(self) -> None:
        """索引向量族校验：重开库换 embedder（dim 不同）必须显式报错。"""
        async def _sig() -> str:
            (v,) = await self._embedder.embed(["probe"])
            return f"{type(self._embedder).__name__}:{len(v)}"

        signature = await _sig()
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT value FROM meta WHERE key = 'embedder'"
            )
            row = await cur.fetchone()
            if row is None:
                await conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('embedder', ?)",
                    (signature,),
                )
                await conn.commit()
            elif row[0] != signature:
                raise RuntimeError(
                    f"索引库向量族不匹配：库为 {row[0]}，当前 {signature}。"
                    "换 embedder 需新库或重建索引。"
                )
        finally:
            await conn.close()

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        if not self._schema_ready:
            await conn.executescript(_SCHEMA)
            await conn.commit()
            self._schema_ready = True
        return conn

    async def index(
        self, doc: SourceDoc, page_descriptions: list[str]
    ) -> int:
        """建块→落图→向量化→入库。返回入库块数。"""
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        await self._assert_meta()
        image_paths: dict[int, str] = {}
        for page_no, png in doc.pages:
            path = self.blobs_dir / f"{_slug(doc.name)}_p{page_no}.png"
            path.write_bytes(png)
            image_paths[page_no] = str(path)

        rows: list[tuple[str, int, str, str, str]] = []  # doc/page/kind/content/img
        # 描述数必须与页数对齐（错位会让页归属系统性错位）
        for (page_no, _png), desc in zip(
            doc.pages, page_descriptions, strict=True
        ):
            if desc.strip():  # 空描述不入库（会以均匀向量污染 top-k）
                rows.append((doc.name, page_no, "page_desc", desc.strip(),
                             image_paths.get(page_no, "")))
        # 文本块按句切；页归属='第N页'标记携带最近页（无标记沿用上一句的页）
        last_page = doc.pages[0][0] if doc.pages else 1
        for sent in _sentences(doc.text):
            page_no = _page_of(sent, doc.pages, last_page)
            last_page = page_no
            rows.append((doc.name, page_no, "text", sent,
                         image_paths.get(page_no, "")))

        contents = [r[3] for r in rows]
        vecs = await self._embedder.embed(contents) if contents else []
        conn = await self._connect()
        try:
            # 同 doc 重 ingest 先清旧块（top-k 不得出现重复块）
            await conn.execute("DELETE FROM chunks WHERE doc = ?", (doc.name,))
            for row, vec in zip(rows, vecs, strict=True):
                await conn.execute(
                    "INSERT INTO chunks (doc, page, kind, content, vec,"
                    " page_image_path) VALUES (?,?,?,?,?,?)",
                    (row[0], row[1], row[2], row[3],
                     json.dumps(vec), row[4]),
                )
            await conn.commit()
        finally:
            await conn.close()
        return len(rows)

    async def retrieve(self, query: str, k: int = 4) -> list[RetrievedChunk]:
        """点积 top-k（向量归一化 ⇒ 点积=余弦）。"""
        (qvec,) = await self._embedder.embed([query])
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT doc, page, kind, content, vec, page_image_path"
                " FROM chunks"
            )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        scored: list[RetrievedChunk] = []
        for doc, page, _kind, content, vec_json, img in rows:
            vec = json.loads(vec_json)
            score = sum(a * b for a, b in zip(qvec, vec, strict=True))
            scored.append(RetrievedChunk(
                content=content, score=score, doc=doc, page=page,
                page_image_path=img,
            ))
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]


def _slug(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_") or "doc"
    # 加 name 哈希后缀：'a b.pdf' 与 'a:b.pdf' 不得共享 blob 互相覆写
    digest = hashlib.sha256(name.encode()).hexdigest()[:6]
    return f"{base}_{digest}"


def _page_of(
    sentence: str, pages: list[tuple[int, bytes]], last_page: int
) -> int:
    """'第N页'标记页归属；无标记沿用最近标记页（首页兜底）。"""
    m = re.match(r"第(\d+)页", sentence)
    if m:
        n = int(m.group(1))
        if any(p == n for p, _ in pages):
            return n
    return last_page if any(p == last_page for p, _ in pages) else (
        pages[0][0] if pages else 1
    )

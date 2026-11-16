"""MRAGStore——多模态 RAG 索引与检索（V11-T2，sqlite 惯例同内核）。

块模型：text 块（全文按句切段）+ page_desc 块（VLM 页面描述）。
多模态证据：每块携带所属页面图像的落盘路径（回答时可直接附图）。
检索：与查询向量点积（向量已归一化，点积=余弦）top-k。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from examples.mrag.embed import HashEmbedding

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


def _sentences(text: str) -> list[str]:
    """按句/换行切段，滤空白；段长上限 200 字符兜底。"""
    parts = re.split(r"(?<=[。．!？?\n])", text)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p if len(p) <= 200 else p[:200])
    return out


class MRAGStore:
    def __init__(self, db_path: Path | str, blobs_dir: Path | str,
                 embedder: HashEmbedding | None = None) -> None:
        self.db_path = Path(db_path)
        self.blobs_dir = Path(blobs_dir)
        self._embedder = embedder or HashEmbedding()
        self._schema_ready = False

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
        image_paths: dict[int, str] = {}
        for page_no, png in doc.pages:
            path = self.blobs_dir / f"{_slug(doc.name)}_p{page_no}.png"
            path.write_bytes(png)
            image_paths[page_no] = str(path)

        rows: list[tuple[str, int, str, str, str]] = []  # doc/page/kind/content/img
        for i, desc in enumerate(page_descriptions):
            page_no = doc.pages[i][0] if i < len(doc.pages) else 1
            rows.append((doc.name, page_no, "page_desc", desc,
                         image_paths.get(page_no, "")))
        # 文本块从全文按句切；页归属按'第N页'前缀（_page_of）
        for sent in _sentences(doc.text):
            page_no = _page_of(sent, doc.pages)
            rows.append((doc.name, page_no, "text", sent,
                         image_paths.get(page_no, "")))

        contents = [r[3] for r in rows]
        vecs = await self._embedder.embed(contents)
        conn = await self._connect()
        try:
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
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", name)
    return base.strip("_") or "doc"


def _page_of(sentence: str, pages: list[tuple[int, bytes]]) -> int:
    """中文文本前缀'第N页'归属；无标记 → 首页。"""
    m = re.match(r"第(\d+)页", sentence)
    if m:
        n = int(m.group(1))
        if any(p == n for p, _ in pages):
            return n
    return pages[0][0] if pages else 1

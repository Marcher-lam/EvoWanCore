"""MRAG 真实链路验证（V11.5）——27b VLM + qwen3 embedding 一条链。

语料：PIL 画 3 张中文票据页（发票/出租车票/审批页）。
链路：LMStudioVLM(qwen3.8-27b-mlx) 描述 → LmStudioEmbedding(
text-embedding-qwen3-embedding-0.6b) 向量化 → 检索 → 27b 带图引用回答。
运行：uv run python -m examples.mrag.realrun [--rebuild]
（实验脚本不进 pytest——零网络红线；报告存 examples/mrag/realrun_report.md）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_BASE = Path(__file__).parent
VLM_MODEL = "qwen3.8-27b-mlx"
EMBED_MODEL = "text-embedding-qwen3-embedding-0.6b"

_PAGES = [
    (1, "增值税电子发票", [
        ("购货方", "杭州云启科技有限公司"),
        ("商品", "服务器 GPU A100 × 2"),
        ("金额", "￥ 385,000.00"),
        ("开票日期", "2026-10-18"),
    ]),
    (2, "出租车发票", [
        ("上车", "2026-10-30 21:40 杭州东站"),
        ("下车", "2026-10-30 22:15 文三路"),
        ("金额", "￥ 86.50"),
        ("车牌", "浙A·D82K1"),
    ]),
    (3, "差旅报销审批单", [
        ("申请人", "张三"),
        ("事由", "北京客户现场部署"),
        ("审批", "同意报销——李四 2026-11-02"),
        ("合计", "￥ 385,086.50"),
    ]),
]

_TEXT = (
    "第1页：增值税电子发票，购货方杭州云启科技有限公司，服务器 GPU A100 两台，"
    "金额 385000 元，开票日期 2026-10-18。\n"
    "第2页：出租车发票，杭州东站到文三路，金额 86.5 元，车牌 浙AD82K1。\n"
    "第3页：差旅报销审批单，申请人张三，审批同意报销，签字李四。"
)

_QUERIES = [
    ("GPU 服务器花了多少钱？", "385,000", 1),
    ("打车从哪到哪、多少钱？", "86.5", 2),
    ("谁审批的报销？", "李四", 3),
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        "/System/Library/Fonts/STHeiti Medium.ttc", size
    )


def _draw_page(title: str, fields: list[tuple[str, str]]) -> bytes:
    import io

    img = Image.new("RGB", (760, 560), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([16, 16, 744, 544], outline="#333", width=2)
    d.text((48, 48), title, fill="black", font=_font(34))
    d.line([48, 108, 712, 108], fill="#888", width=2)
    y = 140
    for k, v in fields:
        d.text((60, y), f"{k}：{v}", fill="black", font=_font(26))
        y += 64
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def main() -> int:
    from examples.docworkbench.lmstudio_vlm import LMStudioVLM
    from examples.mrag.app import MRAGApp
    from examples.mrag.embed import LmStudioEmbedding

    if "--rebuild" in sys.argv:
        for f in (_BASE / "realrun.db",):
            f.unlink(missing_ok=True)
        import shutil

        shutil.rmtree(_BASE / "realrun_blobs", ignore_errors=True)
        print("[rebuild] 旧索引已清")

    vlm = LMStudioVLM(VLM_MODEL)
    app = MRAGApp(
        db_path=_BASE / "realrun.db",
        blobs_dir=_BASE / "realrun_blobs",
        embedder=LmStudioEmbedding(
            "http://localhost:1234/v1", EMBED_MODEL
        ),
        vlm=vlm,
    )

    from examples.mrag.store import SourceDoc

    doc = SourceDoc(
        name="expense-2026-10.pdf",
        pages=[(n, _draw_page(t, f)) for n, t, f in _PAGES],
        text=_TEXT,
    )
    print("[ingest] 3 页 → 27b 逐页描述中（reasoning=low，~7s/页）...")
    n = await app.ingest(doc)
    print(f"[ingest] 入库 {n} 块；VLM 调用 {vlm.calls} 次")

    lines = [
        "# MRAG 真实链路报告（27b + qwen3-embedding）",
        "",
        f"- VLM: `{VLM_MODEL}`（reasoning_effort=low）",
        f"- Embedding: `{EMBED_MODEL}`（L2 归一化）",
        f"- 语料: 3 页 PIL 中文票据 + 全文；索引 {n} 块",
        "",
    ]
    passed = 0
    for query, expect_text, expect_page in _QUERIES:
        hits = await app.retrieve(query, k=3)
        answer = await app.answer(query)
        ok = (
            expect_text in answer.text.replace("￥ ", "")
            and (doc.name, expect_page) in answer.citations
        )
        passed += ok
        lines += [
            f"## {query}  → {'✅' if ok else '❌'}",
            f"- top-1: `p{hits[0].page}` score={hits[0].score:.3f} "
            f"`{hits[0].content[:60]}…`",
            f"- 答案: {answer.text}",
            f"- 引用: {answer.citations}（期望 p.{expect_page} 含 {expect_text}）",
            "",
        ]
        print(f"[query] {query} → {'OK' if ok else 'FAIL'} "
              f"(cites={answer.citations})")

    lines.append(f"## 结论：{passed}/3 通过")
    report = _BASE / "realrun_report.md"
    report.write_text("\n".join(lines))
    print(f"[report] {report}")
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

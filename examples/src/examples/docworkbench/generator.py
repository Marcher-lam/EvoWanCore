"""generate_corpus — 可种子化的合成文档语料生成（spec §5.7）。

random.Random(seed) 局部实例，不污染全局状态；难度控制字段密度与干扰项。
"""

from __future__ import annotations

import random
from collections.abc import Callable

from PIL import Image, ImageDraw

from examples.docworkbench.corpus import Corpus
from examples.docworkbench.tools import validate_form

DIMS = ["export", "extract", "convert", "validate"]


def _make_doc(
    rng: random.Random, i: int, difficulty: float, *, force_scan: bool = False
) -> dict:
    # 高难度：双栏版式加权（region 陷阱更多）、扫描件更多（低对比度字段）
    hard = difficulty >= 0.7
    layouts = ["two_column", "two_column"] if hard else ["two_column"]
    layouts += ["form", "report"]
    layout = rng.choice(layouts)
    kind = (
        "scan"
        if force_scan or rng.random() < (0.5 if hard else 0.3)
        else "native"
    )
    n_fields = 3 + int(difficulty * 6)
    fields = []
    for fi in range(n_fields):
        contrast = round(rng.uniform(0.4, 1.0), 2)
        fields.append(
            {
                "name": f"field_{fi}",
                "value": rng.randrange(100, 99999),
                "contrast": contrast,
                "right_noise": rng.randrange(100, 99999),
            }
        )
    if difficulty > 0.2:
        fields.append({"name": "invoice_no", "value": f"TMP-{rng.randrange(1000, 9999)}0"})

    pages = []
    n_pages = rng.randint(1, 3)
    for pi in range(n_pages):
        # T17 页级对比度：高难扫描件首页结构性低对比（<0.5，感知陷阱载体）；
        # 其余页与原生文档从参数化分布取值（同种子确定）
        if kind == "scan" and hard and pi == 0:
            contrast = round(rng.uniform(0.25, 0.48), 2)
        elif kind == "scan":
            contrast = round(rng.uniform(0.5, 0.95), 2)
        else:
            contrast = 1.0
        pages.append({"layout": layout, "is_chart_scan": False, "contrast": contrast})
    has_chart = rng.random() < (0.9 if hard else 0.5)
    if has_chart:
        pages.append(
            {"layout": "chart", "is_chart_scan": True, "contrast": 1.0 if kind == "native"
             else round(rng.uniform(0.5, 0.95), 2)}
        )

    return {
        "id": f"doc_{i}",
        "kind": kind,
        "layout": layout,
        "has_chart_page": has_chart,
        "pages": pages,
        "fields": fields,
        "difficulty": difficulty,
    }


def _text_fill(contrast: float) -> tuple[int, int, int]:
    """对比度 → 文字灰度：低对比度页文字浅（感知差异载体），高对比度近黑。"""
    v = int(round(255 * (1.0 - min(max(contrast, 0.0), 1.0) * 0.92)))
    return (v, v, v)


def _render_png(doc: dict) -> bytes:
    """增强渲染（T17）：逐页分块——版式（双栏/表单/报表/图表）+ 网格线 + 扫描噪点，
    文字灰度按页 contrast 调整；噪点用 doc_id 派生 rng，同种子逐字节确定。"""
    import io
    import random as _random

    n_fields = len(doc["fields"])
    width = 260

    def _page_height(layout: str) -> int:
        cols = 2 if layout == "two_column" else 1
        rows = max(1, -(-n_fields // cols))
        return max(110, 20 + rows * 16 + 14)

    page_heights = [_page_height(p.get("layout", "report")) for p in doc["pages"]]
    img = Image.new("RGB", (width, sum(page_heights) + 16), "white")
    draw = ImageDraw.Draw(img)
    draw.text((6, 2), f"{doc['id']} [{doc['layout']}/{doc['kind']}]", fill="black")

    y = 16
    for pi, page in enumerate(doc["pages"]):
        contrast = float(page.get("contrast", 1.0))
        fill = _text_fill(contrast)
        layout = page.get("layout", "report")
        page_h = page_heights[pi]
        # 页框 + 网格结构线（表格网格是结构信号）
        draw.rectangle([4, y, width - 4, y + page_h - 6], outline="black")
        if layout == "two_column":
            draw.line([width // 2, y, width // 2, y + page_h - 6], fill="black")
        elif layout == "form":
            row_step = max(16, min(24, page_h // (n_fields + 1)))
            for row in range(1, min(4, n_fields + 1)):
                draw.line([8, y + row * row_step, width - 8, y + row * row_step], fill="black")
        elif layout == "chart":
            draw.rectangle([20, y + 20, width - 20, y + page_h - 26], outline="black")

        # 字段值按版式落位（双栏各半）；对比度决定可读性。
        # 真值全量入画（[:6] 截断曾让 8 字段文档漏 2 个字段——真 VLM 复跑前置修复）
        cols = [12, width // 2 + 8] if layout == "two_column" else [12]
        for i, f in enumerate(doc["fields"]):
            cx = cols[i % len(cols)]
            cy = y + 10 + (i // len(cols)) * 16
            draw.text((cx, cy), f"{f['name']}={f['value']}", fill=fill)
        draw.text((width - 70, y + page_h - 18), f"p{pi} c={contrast:.2f}",
                  fill=_text_fill(min(contrast + 0.2, 1.0)))

        # 扫描噪点：确定性（doc/页派生种子），低对比度页更密
        if doc["kind"] == "scan":
            noise_rng = _random.Random(f"{doc['id']}-p{pi}")
            n_dots = int(40 * (1.2 - contrast))
            for _ in range(n_dots):
                nx = noise_rng.randrange(6, width - 6)
                ny = noise_rng.randrange(y + 2, y + page_h - 8)
                draw.point((nx, ny), fill=(160, 160, 160))
        y += page_h

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _task_for(
    dim: str, doc: dict, rng: random.Random, seq: int, *, perceptual: bool = False
) -> tuple[dict, dict, Callable]:
    """(task, ground_truth, acceptance_script) —— 验收脚本是纯 Python 规则。"""
    tid = f"{dim}_{doc['id']}_{seq}"
    if dim == "export":
        def check_export(result: dict) -> dict:
            expected = len(doc["pages"])
            got = len(result.get("exported_pages", []))
            return {
                "passed": got == expected,
                "checks": [{"check": "page_count", "expect": expected, "got": got,
                            "detail": f"导出应含全部 {expected} 页"}],
            }
        task = {"id": tid, "dimension": dim, "doc_id": doc["id"], "goal": "完整导出全部页面"}
        truth = {
            "expected_result": {
                "exported_pages": doc["pages"], "format": "xlsx", "doc_id": doc["id"]
            }
        }
        return task, truth, check_export

    if dim == "extract":
        def check_extract_submit(result: dict) -> dict:
            """T18 image-based：判 submit 交付的 fields 对照渲染真值（程序化，不信自述）。"""
            expected = {f["name"]: f["value"] for f in doc["fields"]}
            got = result.get("fields") or {}
            return {
                "passed": got == expected,
                "checks": [{"check": "fields_match_truth", "expect_n": len(expected),
                            "detail": "提交字段须与渲染真值一致（读图判分）"}],
            }
        # T22：感知语料（v3 实验）中 extract 全量 image-based；文本语料保持 T18 判分
        task = {"id": tid, "dimension": dim, "doc_id": doc["id"],
                "goal": "读图抽取全部字段并提交"}
        if perceptual:
            task["image_required"] = True
        truth = {"expected_result": {
            "fields": {f["name"]: f["value"] for f in doc["fields"]}, "source": "image"}}
        return task, truth, check_extract_submit

    if dim == "convert":
        def check_convert(result: dict) -> dict:
            ok = not result.get("lossy") and result.get("dpi", 0) >= 150
            return {
                "passed": ok,
                "checks": [{"check": "no_field_loss", "detail": "转换不得丢字段(dpi>=150)"}],
            }
        task = {"id": tid, "dimension": dim, "doc_id": doc["id"], "goal": "无损格式转换"}
        truth = {"expected_result": {**doc, "lossy": False, "dpi": 200,
                                     "export_ready": True}}
        return task, truth, check_convert

    def check_validate(result: dict) -> dict:
        res = validate_form(doc)
        return {
            "passed": result == res,
            "checks": [{"check": "validations_match", "detail": "校验结论须与真值一致"}],
        }
    task = {"id": tid, "dimension": dim, "doc_id": doc["id"], "goal": "校验表单字段"}
    truth = {"expected_result": validate_form(doc)}
    return task, truth, check_validate


def generate_corpus(
    seed: int, size: int, difficulty: float, *, perceptual: bool = False
) -> Corpus:
    rng = random.Random(seed)
    # 感知语料：extract 维度文档结构性为扫描件（i%4==1 → DIMS[1]=extract），
    # 低对比度读图陷阱不再依赖扫描概率彩票（门的验证场与保留集评分稳定性）
    documents = [
        _make_doc(rng, i, difficulty, force_scan=perceptual and i % len(DIMS) == 1)
        for i in range(size)
    ]

    tasks: list[dict] = []
    ground_truth: dict = {}
    scripts: dict[str, Callable] = {}
    seq = 0
    for i, doc in enumerate(documents):
        dim = DIMS[i % len(DIMS)]  # 维度轮转保证均衡
        task, truth, script = _task_for(dim, doc, rng, seq, perceptual=perceptual)
        tasks.append(task)
        ground_truth[task["id"]] = truth
        scripts[task["id"]] = script
        seq += 1

    return Corpus(
        documents=documents,
        tasks=tasks,
        ground_truth=ground_truth,
        acceptance_scripts=scripts,
        assets={d["id"]: _render_png(d) for d in documents},
    )

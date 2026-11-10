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


def _make_doc(rng: random.Random, i: int, difficulty: float) -> dict:
    # 高难度：双栏版式加权（region 陷阱更多）、扫描件更多（低对比度字段）
    hard = difficulty >= 0.7
    layouts = ["two_column", "two_column"] if hard else ["two_column"]
    layouts += ["form", "report"]
    layout = rng.choice(layouts)
    kind = "scan" if rng.random() < (0.5 if hard else 0.3) else "native"
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

    pages = [{"layout": layout, "is_chart_scan": False} for _ in range(rng.randint(1, 3))]
    has_chart = rng.random() < (0.9 if hard else 0.5)
    if has_chart:
        pages.append({"layout": "chart", "is_chart_scan": True})

    return {
        "id": f"doc_{i}",
        "kind": kind,
        "layout": layout,
        "has_chart_page": has_chart,
        "pages": pages,
        "fields": fields,
        "difficulty": difficulty,
    }


def _render_png(doc: dict) -> bytes:
    img = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.text((8, 6), f"{doc['id']} [{doc['layout']}/{doc['kind']}]", fill="black")
    for i, f in enumerate(doc["fields"][:6]):
        draw.text((8, 24 + i * 14), f"{f['name']}={f['value']}", fill="black")
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _task_for(dim: str, doc: dict, rng: random.Random, seq: int) -> tuple[dict, dict, Callable]:
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
        def check_extract(result: dict) -> dict:
            expected = {f["name"]: f["value"] for f in doc["fields"]}
            return {
                "passed": result.get("fields") == expected and not result.get("contaminated"),
                "checks": [{"check": "fields_match", "expect_n": len(expected),
                            "detail": "字段值须与真值一致且未被串栏污染"}],
            }
        task = {"id": tid, "dimension": dim, "doc_id": doc["id"], "goal": "抽取全部字段"}
        truth = {"expected_result": {"fields": {f["name"]: f["value"] for f in doc["fields"]},
                                     "contaminated": False}}
        return task, truth, check_extract

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


def generate_corpus(seed: int, size: int, difficulty: float) -> Corpus:
    rng = random.Random(seed)
    documents = [_make_doc(rng, i, difficulty) for i in range(size)]

    tasks: list[dict] = []
    ground_truth: dict = {}
    scripts: dict[str, Callable] = {}
    seq = 0
    for i, doc in enumerate(documents):
        dim = DIMS[i % len(DIMS)]  # 维度轮转保证均衡
        task, truth, script = _task_for(dim, doc, rng, seq)
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

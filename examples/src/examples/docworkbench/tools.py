"""文档工具集 — 环境的"可学习规律"载体（spec §5.7 隐藏约束）。

约束集中在 HIDDEN_CONSTRAINTS 常量表：真值可审计，供记忆审计对照。
朴素调用（不知道约束）会真实失败/丢页——这是探索闭环要学的东西。
"""

from __future__ import annotations

# 每条约束：维度、规则、朴素调用是否失败
HIDDEN_CONSTRAINTS: list[dict] = [
    {
        "id": "export_needs_convert",
        "dimension": "export",
        "rule": "xlsx 导出对含图表扫描页的文档静默丢页；须先 convert_format(dpi=200)",
        "naive_fails": True,
        "claim": {"action": "convert_format", "param": "dpi", "op": ">=", "value": 200},
    },
    {
        "id": "extract_two_column_region",
        "dimension": "extract",
        "rule": "双栏版式 extract_fields 必须显式传 region='left'，否则字段串栏",
        "naive_fails": True,
        "claim": {"action": "extract_fields", "param": "region", "op": "==", "value": "left"},
    },
    {
        "id": "validate_tmp_prefix",
        "dimension": "validate",
        "rule": "invoice_no 以 TMP 开头时跳过校验和检查，恒为有效",
        "naive_fails": False,
        "claim": {"action": "validate_form", "param": "skip_when_prefix",
                  "op": "==", "value": "TMP"},
    },
    {
        "id": "convert_low_dpi_loses_fields",
        "dimension": "convert",
        "rule": "扫描件 convert_format(dpi<150) 会丢失低对比度字段",
        "naive_fails": True,
        "claim": {"action": "convert_format", "param": "dpi", "op": ">=", "value": 150},
    },
    {
        "id": "export_non_xlsx_bypass",
        "dimension": "export",
        "rule": "图表丢页分支仅作用于 format==xlsx；显式非 xlsx 格式导出保留全部页",
        "naive_fails": False,
        "claim": {"action": "export_document", "param": "format", "op": "!=", "value": "xlsx"},
    },
    {
        "id": "perception_low_contrast",
        "dimension": "extract",
        "rule": "低对比度页(contrast<0.5)读图前须 convert_format(dpi>=150) 提升对比度，"
                "否则读图必错",
        "naive_fails": True,
        "claim": {"action": "convert_format", "param": "dpi", "op": ">=", "value": 150},
    },
]

_REQUIRED_DPI = 150
_SAFE_EXPORT_DPI = 200


def parse_document(doc: dict) -> dict:
    """结构解析：返回页数、版式、字段名（只读，无陷阱）。"""
    return {
        "doc_id": doc["id"],
        "pages": len(doc["pages"]),
        "layouts": [p["layout"] for p in doc["pages"]],
        "field_names": [f["name"] for f in doc["fields"]],
        "has_chart_page": bool(doc.get("has_chart_page")),
    }


def extract_fields(doc: dict, region: str | None = None) -> dict:
    """字段抽取。隐藏约束：双栏版式不传 region → 左右串栏，结果被污染。"""
    if doc.get("layout") == "two_column" and region != "left":
        merged = {}
        for f in doc["fields"]:
            merged[f["name"]] = f.get("right_noise", f["value"])  # 串到右栏噪声
        return {"fields": merged, "contaminated": True}
    return {"fields": {f["name"]: f["value"] for f in doc["fields"]}, "contaminated": False}


def extract_fields_from_image(doc: dict) -> dict:
    """感知化抽取（T18）：只给图片引用与页对比度——字段值只在渲染 PNG 与真值表。"""
    return {
        "image_ref": doc["id"],
        "pages": [
            {"page": i, "contrast": float(p.get("contrast", 1.0))}
            for i, p in enumerate(doc["pages"])
        ],
    }


def _normalize_value(value):
    """真 VLM 常把数字读成字符串（"18288"）——纯数字串归一为 int，
    其余（TMP-72780 等）保持原样；判分对照值语义。"""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def submit_extracted_fields(doc_id: str, fields: dict | str) -> dict:
    """交付抽取结果（T18）：模型读图后提交；交付载荷进证据链，由验收对照真值判分。

    字段值经 _normalize_value 归一（字符串数字→int）；fields 为 JSON 字符串时
    先解析（27b 真模型实测常把 dict 序列化成 str 传入，曾致 handler 连环
    ValueError → run 空转 23 次提议）。
    """
    import json as _json

    if isinstance(fields, str):
        try:
            fields = _json.loads(fields)
        except ValueError:
            fields = {}
    normalized = {k: _normalize_value(v) for k, v in dict(fields).items()}
    return {"doc_id": doc_id, "fields": normalized, "source": "image_read"}


def convert_format(doc: dict, dpi: int = 200) -> dict:
    """格式转换。隐藏约束：扫描件低 dpi 丢字段；高 dpi 为安全导出做准备。

    T18：dpi>=150 时页对比度提升到 1.0（感知约束的可解除载体——convert 后读图全对）。
    """
    if doc.get("kind") == "scan" and dpi < _REQUIRED_DPI:
        kept = [f for f in doc["fields"] if f.get("contrast", 1.0) >= 0.8]
        return {**doc, "fields": kept, "lossy": True, "dpi": dpi}
    if dpi >= _REQUIRED_DPI:
        pages = [{**p, "contrast": 1.0} for p in doc["pages"]]
        render_scale = 2  # 真 VLM OCR 精度（27b 探针：低对比 1x 首位数字错，2x 全对）
    else:
        pages = doc["pages"]
        render_scale = 1
    return {**doc, "pages": pages, "lossy": False, "dpi": dpi,
            "render_scale": render_scale,
            "export_ready": dpi >= _SAFE_EXPORT_DPI}


def export_document(doc: dict, format: str = "xlsx") -> dict:
    """导出。隐藏约束：xlsx + 图表扫描页 → 静默丢页，除非 doc 已 export_ready。"""
    pages = doc["pages"]
    if format == "xlsx" and doc.get("has_chart_page") and not doc.get("export_ready"):
        exported = [p for p in pages if not p.get("is_chart_scan")]
    else:
        exported = list(pages)
    return {"exported_pages": exported, "format": format, "doc_id": doc["id"]}


def validate_form(doc: dict) -> dict:
    """表单校验。隐藏约束：TMP 前缀发票号跳过校验和，恒有效。"""
    results = {}
    for f in doc["fields"]:
        if f["name"] == "invoice_no":
            valid = str(f["value"]).startswith("TMP") or _checksum_ok(f["value"])
            results[f["name"]] = "valid" if valid else "invalid"
        else:
            results[f["name"]] = "valid" if f["value"] not in ("", None) else "invalid"
    return {"validations": results, "all_valid": all(v == "valid" for v in results.values())}


def _checksum_ok(value) -> bool:
    s = str(value)
    return len(s) >= 8 and s[-1].isdigit() and (int(s[:-1].replace("-", "")) % 7 == int(s[-1]))

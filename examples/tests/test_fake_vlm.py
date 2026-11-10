"""T19 WorkbenchFakeVLM：对比度条件读图（第四隐藏约束的可测载体，Review Focus #3）。"""

from agent_core.models import InferRequest
from examples.docworkbench.corpus import Corpus
from examples.docworkbench.fake_vlm import WorkbenchFakeVLM
from examples.docworkbench.generator import generate_corpus
from examples.docworkbench.tools import convert_format


def _corpus() -> Corpus:
    return generate_corpus(seed=5, size=8, difficulty=0.8)


def _doc(corpus: Corpus, doc_id: str) -> dict:
    return next(d for d in corpus.documents if d["id"] == doc_id)


def _request_for(doc_id: str) -> InferRequest:
    return InferRequest(
        model_id="fake-vlm",
        messages=[
            {"role": "system", "content": f"读图抽取 doc_id={doc_id} 并提交字段"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"image_ref={doc_id}"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                ],
            },
        ],
    )


def _submitted_fields(resp) -> tuple[str, dict]:
    assert resp.tool_calls, "读图行为必须以 submit_extracted_fields 工具调用交付"
    call = resp.tool_calls[0]
    assert call["name"] == "submit_extracted_fields"
    return call["arguments"]["doc_id"], call["arguments"]["fields"]


def _extract_doc(corpus: Corpus) -> dict:
    task = next(t for t in corpus.tasks if t["dimension"] == "extract")
    return _doc(corpus, task["doc_id"])


async def test_fake_vlm_correct_on_high_contrast() -> None:
    """全页对比度 >=0.5 的文档：FakeVLM 读出真值并提交。"""
    corpus = _corpus()
    doc = next(
        d
        for d in corpus.documents
        if all(p.get("contrast", 1.0) >= 0.5 for p in d["pages"])
    )
    vlm = WorkbenchFakeVLM(corpus, derived={})
    caps = vlm.capabilities()
    assert caps.model_id == "fake-vlm"
    assert {m.value for m in caps.inputs} == {"text", "image"}

    doc_id, fields = _submitted_fields(await vlm.infer(_request_for(doc["id"])))
    assert doc_id == doc["id"]
    assert fields == {f["name"]: f["value"] for f in doc["fields"]}, "高对比度页应读出真值"


async def test_fake_vlm_corrupts_low_contrast() -> None:
    """存在 contrast<0.5 页：读出确定性错值（hash 扰动），且同请求结果确定。"""
    corpus = _corpus()
    doc = next(
        d
        for d in corpus.documents
        if any(p.get("contrast", 1.0) < 0.5 for p in d["pages"])
    )
    truth = {f["name"]: f["value"] for f in doc["fields"]}
    vlm = WorkbenchFakeVLM(corpus, derived={})

    _doc_id, fields1 = _submitted_fields(await vlm.infer(_request_for(doc["id"])))
    _doc_id, fields2 = _submitted_fields(await vlm.infer(_request_for(doc["id"])))
    assert fields1 != truth, "低对比度页必须读错（感知陷阱）"
    assert fields1 == fields2, "错值必须确定性（可学习/可验收）"
    assert set(fields1) == set(truth), "字段名不丢，值错"


async def test_after_convert_reads_correctly() -> None:
    """convert_format(dpi>=150) 提升页对比度后：同一文档读出真值（约束可解除）。"""
    corpus = _corpus()
    doc = next(
        d
        for d in corpus.documents
        if any(p.get("contrast", 1.0) < 0.5 for p in d["pages"])
    )
    truth = {f["name"]: f["value"] for f in doc["fields"]}
    converted = convert_format(doc, dpi=200)
    assert all(p["contrast"] == 1.0 for p in converted["pages"])

    vlm = WorkbenchFakeVLM(corpus, derived={doc["id"]: converted})
    _doc_id, fields = _submitted_fields(await vlm.infer(_request_for(doc["id"])))
    assert fields == truth, "convert 后应读出真值（读图前须提升对比度）"



async def test_fake_vlm_with_rule_converts_first() -> None:
    """T23：低对比度 + 上下文已发布规则 → FakeVLM 先 convert（真 VLM 的理性行为）；
    规则缺位则保持读错。这是探索闭环因果生效的机制载体。"""
    from agent_core.models import InferRequest

    corpus = _corpus()
    doc = next(
        d
        for d in corpus.documents
        if any(p.get("contrast", 1.0) < 0.5 for p in d["pages"])
    )
    vlm = WorkbenchFakeVLM(corpus, derived={})
    rule_msg = InferRequest(
        model_id="fake-vlm",
        messages=[
            {"role": "system", "content": (
                "Relevant memory:\n- [rule|verified|claim:convert_format] "
                "低对比度页(contrast<0.5)读图前须 convert_format(dpi>=150) "
                "提升对比度，否则读图必错")},
            {"role": "user", "content": f"image_ref={doc['id']} 读图抽取"},
        ],
    )
    resp = await vlm.infer(rule_msg)
    assert resp.tool_calls and resp.tool_calls[0]["name"] == "convert_format", (
        "有规则时低对比度文档应先转换（而非直接提交错值）"
    )
    assert resp.tool_calls[0]["arguments"]["dpi"] >= 150


async def test_fake_vlm_rule_detection_scoped_to_claim_action() -> None:
    """I2（终评审）：规则触发按 claim 动作判定——export 声明的规则文本提及
    convert_format 不得触发读图转换（归因隔离）；convert_format 声明才触发。"""
    from agent_core.models import InferRequest

    corpus = _corpus()
    doc = next(
        d
        for d in corpus.documents
        if any(p.get("contrast", 1.0) < 0.5 for p in d["pages"])
    )
    vlm = WorkbenchFakeVLM(corpus, derived={})

    def _req(memory_line: str) -> InferRequest:
        return InferRequest(
            model_id="fake-vlm",
            messages=[
                {"role": "system", "content": f"Relevant memory:\n{memory_line}"},
                {"role": "user", "content": f"image_ref={doc['id']} 读图抽取"},
            ],
        )

    # export 声明的规则（文本提及 convert_format）——不触发
    export_rule = (
        "- [rule|verified|claim:export_document] 须先 convert_format(dpi=200) 再导出"
    )
    resp = await vlm.infer(_req(export_rule))
    assert resp.tool_calls[0]["name"] == "submit_extracted_fields", (
        "export 声明的规则不得驱动读图转换（归因隔离）"
    )
    # convert_format 声明的规则——触发
    convert_rule = (
        "- [rule|verified|claim:convert_format] 低对比度页须先转换提升对比度"
    )
    resp2 = await vlm.infer(_req(convert_rule))
    assert resp2.tool_calls[0]["name"] == "convert_format"

"""T10 LLMReflector：结构化声明提案、evidence 白名单、畸形输出容错。"""

from agent_core.models import (
    FakeModelAdapter,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
)
from agent_explore.reflector import LLMReflector


class _Fam:
    value = "variant"


class _Task:
    id = "extract_doc_1"
    title = "双栏抽取"
    family = _Fam()
    objective = {}


class _Outcome:
    task_id = "extract_doc_1"
    title = "双栏抽取"
    status = "passed"
    evidence_refs = ["e1", "e2"]


def _gateway(responses: list[InferResponse]) -> tuple[ModelGateway, FakeModelAdapter]:
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-r", inputs={Modality.TEXT}), list(responses)
    )
    gw = ModelGateway()
    gw.register(fake)
    return gw, fake


async def test_reflector_structured_output() -> None:
    """合法 JSON → 提案结构正确（claim 双层）；evidence 白名单：LLM 自报 refs 被丢弃。"""
    import json

    llm_output = {
        "proposals": [
            {
                "statement": "双栏版式抽取必须传 region=left，否则串栏",
                "claim": {
                    "dimension": "extract",
                    "action": "extract_fields",
                    "params": {"region": "left"},
                },
                "applicability": {"app": "workbench"},
                "evidence_refs": ["FAKE_LLM_REF"],  # 必须被白名单过滤
            }
        ]
    }
    gw, _fake = _gateway([InferResponse(text=json.dumps(llm_output))])
    reflector = LLMReflector(gateway=gw, model_id="m-r")

    proposals = await reflector(_Task(), _Outcome())
    assert len(proposals) == 1
    p = proposals[0]
    assert p["kind"] == "rule"
    assert p["content"]["statement"] == "双栏版式抽取必须传 region=left，否则串栏"
    assert p["content"]["claim"] == {
        "dimension": "extract",
        "action": "extract_fields",
        "params": {"region": "left"},
    }
    assert p["applicability"] == {"app": "workbench"}
    assert p["evidence_refs"] == ["e1", "e2"], "evidence 只取 outcome 白名单"


async def test_reflector_malformed_returns_empty() -> None:
    """畸形输出：校验-重试一轮后仍坏 → []（不抛错、不产生候选）。"""
    gw, fake = _gateway(
        [InferResponse(text="这不是 JSON"), InferResponse(text="still not json")]
    )
    reflector = LLMReflector(gateway=gw, model_id="m-r")

    proposals = await reflector(_Task(), _Outcome())
    assert proposals == []
    assert len(fake.recorded_requests) == 2, "畸形应触发一次带错误提示的重试"


class _OutcomeWithTrace:
    task_id = "convert_doc_2"
    title = "转换练习"
    status = "passed"
    evidence_refs = ["e1"]
    actions = [
        {"capability_id": "convert_format", "arguments": {"doc_id": "doc_2", "dpi": 200},
         "status": "succeeded"},
    ]


async def test_reflector_prompt_carries_action_trace() -> None:
    """T16 诊断迭代：提示须含工具语义 + 动作轨迹（LLM 才能推断因果参数约束）。"""
    import json

    llm_output = {
        "proposals": [
            {
                "statement": "扫描件转换须 dpi>=150 否则丢字段",
                "claim": {"dimension": "convert", "action": "convert_format",
                          "params": {"dpi": 200}},
                "applicability": {"app": "workbench"},
            }
        ]
    }
    gw, fake = _gateway([InferResponse(text=json.dumps(llm_output))])
    reflector = LLMReflector(gateway=gw, model_id="m-r")
    proposals = await reflector(_Task(), _OutcomeWithTrace())
    assert proposals and proposals[0]["content"]["claim"]["params"] == {"dpi": 200}
    prompt = " ".join(m["content"] for m in fake.recorded_requests[0].messages)
    assert "convert_format" in prompt, "提示须含工具语义"
    assert "dpi" in prompt and '"status": "succeeded"' in prompt.replace("'", '"'), (
        "提示须含动作轨迹（LLM 推断因果的依据）"
    )


class _OutcomeWithFeedback:
    task_id = "extract_doc_2"
    title = "抽取练习"
    status = "failed"
    evidence_refs = ["e1"]
    actions = [{"capability_id": "extract_fields",
                "arguments": {"doc_id": "doc_2"}, "status": "succeeded"}]
    verifier_checks = [{"check": "fields_match", "detail": "字段值须与真值一致且未被串栏污染"}]


async def test_reflector_prompt_carries_acceptance_feedback() -> None:
    """T16 迭代 2：提示须含验收反馈（失败 detail）——环境反馈是因果推断的关键证据。"""
    import json

    llm_output = {"proposals": []}
    gw, fake = _gateway([InferResponse(text=json.dumps(llm_output))])
    reflector = LLMReflector(gateway=gw, model_id="m-r")
    assert await reflector(_Task(), _OutcomeWithFeedback()) == []
    prompt = " ".join(m["content"] for m in fake.recorded_requests[0].messages)
    assert "串栏污染" in prompt, "验收反馈须进入提示"
    assert "acceptance_feedback" in prompt or "ACCEPTANCE FEEDBACK" in prompt


async def test_reflector_prompt_grounded_and_names_preprocessing() -> None:
    """T23 迭代：提示须含 (a) 参数接地纪律（防编造因果）；(b) 扫描件读错时的
    预处理杠杆提示（convert_format dpi）——阈值仍由评测门行为验证，非泄漏。"""
    gw, fake = _gateway([InferResponse(text='{"proposals": []}')])
    reflector = LLMReflector(gateway=gw, model_id="m-r")
    await reflector(_Task(), _OutcomeWithTrace())
    prompt = fake.recorded_requests[0].messages[0]["content"]
    assert "trace" in prompt.lower() and "only parameters" in prompt, (
        "须要求 claim 只引用 trace 中实际出现/缺省的参数"
    )
    assert "convert_format" in prompt and "scan" in prompt.lower(), (
        "须提示扫描件读错时的预处理杠杆（convert_format dpi）"
    )

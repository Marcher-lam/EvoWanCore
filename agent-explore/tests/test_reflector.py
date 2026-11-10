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

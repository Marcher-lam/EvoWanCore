"""LMStudioVLM 适配器：服务在 localhost:1234 时集成实测，不在时 skip。"""

import base64 as b64

import pytest
from agent_core.models import InferRequest, Modality

lmstudio = pytest.importorskip("httpx")


def _server_up() -> bool:
    try:
        import httpx

        return httpx.get("http://localhost:1234/v1/models", timeout=3).status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def vlm():
    if not _server_up():
        pytest.skip("LM Studio (localhost:1234) 未运行")
    from examples.docworkbench.lmstudio_vlm import LMStudioVLM

    return LMStudioVLM("lfm3-omni-2b")


async def test_vlm_reads_image_and_calls_tool(vlm, tmp_path) -> None:
    """端到端：图片进 → submit_extracted_fields 工具调用出，且读出真值字段。"""
    from examples.docworkbench.generator import generate_corpus

    corpus = generate_corpus(seed=5, size=8, difficulty=0.8)
    doc = next(d for d in corpus.documents if d["kind"] == "native")
    truth = {f["name"]: f["value"] for f in doc["fields"]}
    data_url = (
        f"data:image/png;base64,{b64.b64encode(corpus.assets[doc['id']]).decode()}"
    )
    resp = await vlm.infer(
        InferRequest(
            model_id=vlm.capabilities().model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Read field_0 from the image and call "
                                "submit_extracted_fields with it."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "submit_extracted_fields",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "doc_id": {"type": "string"},
                                "fields": {"type": "object"},
                            },
                            "required": ["doc_id", "fields"],
                        },
                    },
                }
            ],
        )
    )
    assert vlm.capabilities().inputs >= {Modality.TEXT, Modality.IMAGE}
    assert resp.tool_calls, "真 VLM 须以工具调用交付读图结果"
    call = resp.tool_calls[0]
    assert call["name"] == "submit_extracted_fields"
    fields = call["arguments"]["fields"]
    assert str(fields.get("field_0")) == str(truth["field_0"]), "field_0 读值正确"

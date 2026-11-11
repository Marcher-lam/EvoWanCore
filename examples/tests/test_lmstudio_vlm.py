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


def _server_has_model(model_id: str) -> bool:
    """模型存在于 LM Studio 目录（JIT 卸载无妨——请求会自动加载，首调含加载耗时）。"""
    try:
        import httpx

        data = httpx.get("http://localhost:1234/api/v0/models", timeout=3).json()
        return any(m["id"] == model_id for m in data["data"])
    except Exception:
        return False


@pytest.fixture(scope="module")
def vlm_27b():
    if not _server_has_model("qwen3.8-27b-mlx"):
        pytest.skip("qwen3.8-27b-mlx 未加载于 LM Studio")
    from examples.docworkbench.lmstudio_vlm import LMStudioVLM

    return LMStudioVLM("qwen3.8-27b-mlx")


async def test_vlm_27b_reads_image_via_tool_call(vlm_27b) -> None:
    """T1：27b 生产路径钉子——image+tool_call（适配器默认 auto）读值正确。

    探针（2026-11-11）用 required 3/3；本测用生产 auto 形态——若 27b 在 auto 下
    不发 tool_call，即为生产路径缺陷而非测试问题。~60-90s 真模型调用。
    """
    from examples.docworkbench.generator import generate_corpus

    corpus = generate_corpus(seed=5, size=8, difficulty=0.8)
    doc = next(d for d in corpus.documents if d["kind"] == "native")
    truth = {f["name"]: f["value"] for f in doc["fields"]}
    data_url = f"data:image/png;base64,{b64.b64encode(corpus.assets[doc['id']]).decode()}"
    resp = await vlm_27b.infer(
        InferRequest(
            model_id="qwen3.8-27b-mlx",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Read field_0, field_1, field_2 from this image and call "
                        "submit_extracted_fields with the values you read."
                    )},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            tools=[{
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
            }],
        )
    )
    assert resp.tool_calls, "生产 auto 形态下 27b 须以工具调用交付读图结果"
    call = resp.tool_calls[0]
    assert call["name"] == "submit_extracted_fields"
    fields = call["arguments"]["fields"]
    for k in ("field_0", "field_1", "field_2"):
        assert str(fields.get(k)) == str(truth[k]), f"{k} 读值须正确"

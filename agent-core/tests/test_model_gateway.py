"""T8 ModelGateway：能力协商路由、双接口契约、Fake 适配器。"""

import pytest
from agent_core.models import (
    FakeModelAdapter,
    InferRequest,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
    NoModelAvailable,
    SessionConfig,
    SessionEvent,
)


def _caps(**over) -> ModelCapabilities:
    base = dict(
        model_id="m-chat",
        inputs={Modality.TEXT},
        outputs={Modality.TEXT},
        interaction={"batch", "streaming"},
        tools={"native_tool_call"},
        constraints={},
    )
    base.update(over)
    return ModelCapabilities(**base)


def test_route_matches_requirements() -> None:
    gw = ModelGateway()
    gw.register(FakeModelAdapter(_caps(), []))
    adapter = gw.route({"inputs": [Modality.TEXT], "interaction": "batch"})
    assert adapter.capabilities().model_id == "m-chat"


def test_route_no_match_raises() -> None:
    gw = ModelGateway()
    gw.register(FakeModelAdapter(_caps(), []))
    with pytest.raises(NoModelAvailable):
        gw.route({"inputs": [Modality.IMAGE]})  # 纯文本模型接不了图


def test_capabilities_declared_independently() -> None:
    # 音频输入 + 音频输出 ≠ 实时会话：能力独立声明
    audio_batch = _caps(
        model_id="m-audio-batch",
        inputs={Modality.AUDIO},
        outputs={Modality.AUDIO},
        interaction={"batch"},
    )
    gw = ModelGateway()
    gw.register(FakeModelAdapter(audio_batch, []))
    with pytest.raises(NoModelAvailable):
        gw.route({"interaction": "realtime"})


async def test_fake_adapter_scripted_responses() -> None:
    responses = [
        InferResponse(text="第一跳"),
        InferResponse(text="第二跳", tool_calls=[{"name": "export", "arguments": {}}]),
    ]
    fake = FakeModelAdapter(_caps(), responses)
    req = InferRequest(model_id="m-chat", messages=[{"role": "user", "content": "hi"}])
    r1 = await fake.infer(req)
    r2 = await fake.infer(req)
    assert r1.text == "第一跳"
    assert r2.tool_calls[0]["name"] == "export"
    assert fake.recorded_requests == [req, req]
    with pytest.raises(AssertionError, match="脚本耗尽"):
        await fake.infer(req)


async def test_fake_adapter_exposes_capabilities() -> None:
    fake = FakeModelAdapter(_caps(), [])
    assert "batch" in fake.capabilities().interaction


def test_session_contracts_are_pydantic() -> None:
    cfg = SessionConfig(model_id="m-rt", voice="alloy", instructions="你是文档助手")
    assert SessionConfig.model_validate_json(cfg.model_dump_json()) == cfg
    ev = SessionEvent(type="interrupted", payload={"at_ms": 1200})
    assert ev.type == "interrupted"


def test_session_adapter_lookup() -> None:
    from agent_core.models import FakeSessionAdapter

    gw = ModelGateway()
    fake_sess = FakeSessionAdapter(_caps(model_id="m-rt", interaction={"realtime"}))
    gw.register_session(fake_sess)
    assert gw.session_adapter("m-rt") is fake_sess
    with pytest.raises(KeyError):
        gw.session_adapter("m-nosuch")

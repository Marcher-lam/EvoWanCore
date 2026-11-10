"""T9 OpenAI 兼容 infer 适配器：请求映射与响应解析（全 mock，零网络）。"""

import json
from types import SimpleNamespace as NS

import agent_adapters.openai_compat as oc
import pytest
from agent_core.models import InferRequest, Modality, ModelCapabilities


def _caps() -> ModelCapabilities:
    return ModelCapabilities(model_id="glm-x", inputs={Modality.TEXT, Modality.IMAGE})


def _make_client(captured: dict, response: NS) -> object:
    async def create(**kwargs):
        captured["kwargs"] = kwargs
        return response

    return NS(chat=NS(completions=NS(create=create)))


def _response(content="ok", tool_calls=None, total_tokens=42) -> NS:
    tcs = None
    if tool_calls is not None:
        tcs = [
            NS(id=f"call_{i}", function=NS(name=name, arguments=json.dumps(args)))
            for i, (name, args) in enumerate(tool_calls)
        ]
    usage = NS(total_tokens=total_tokens, prompt_tokens=10, completion_tokens=total_tokens - 10)
    return NS(choices=[NS(message=NS(content=content, tool_calls=tcs))], usage=usage)


@pytest.fixture
def patch_client(monkeypatch):
    def install(response: NS) -> dict:
        captured: dict = {}
        monkeypatch.setattr(oc, "AsyncOpenAI", lambda **kw: _make_client(captured, response))
        return captured

    return install


async def test_request_mapping(patch_client) -> None:
    captured = patch_client(_response())
    adapter = oc.OpenAICompatAdapter(
        base_url="https://api.fake.test/v1",
        api_key="sk-x",
        capabilities=_caps(),
    )
    req = InferRequest(
        model_id="glm-x",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "export"}}],
        structured_output={"name": "Fields", "schema": {"type": "object"}},
    )
    await adapter.infer(req)
    kw = captured["kwargs"]
    assert kw["model"] == "glm-x"
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    assert kw["tools"] == req.tools  # 透传
    assert kw["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Fields", "schema": {"type": "object"}},
    }


async def test_response_parsing(patch_client) -> None:
    patch_client(
        _response(
            content="已导出",
            tool_calls=[("export", {"format": "xlsx"}), ("parse", {"page": 2})],
            total_tokens=99,
        )
    )
    adapter = oc.OpenAICompatAdapter(
        base_url="https://api.fake.test/v1", api_key="sk-x", capabilities=_caps()
    )
    resp = await adapter.infer(InferRequest(model_id="glm-x", messages=[]))
    assert resp.text == "已导出"
    assert [c["name"] for c in resp.tool_calls] == ["export", "parse"]
    assert resp.tool_calls[0]["arguments"] == {"format": "xlsx"}
    assert resp.usage["total_tokens"] == 99


async def test_usage_and_tools_defaulting(patch_client) -> None:
    resp_obj = NS(choices=[NS(message=NS(content="x", tool_calls=None))], usage=None)
    captured = patch_client(resp_obj)
    adapter = oc.OpenAICompatAdapter(
        base_url="https://api.fake.test/v1", api_key="sk-x", capabilities=_caps()
    )
    resp = await adapter.infer(InferRequest(model_id="glm-x", messages=[]))
    assert resp.usage.get("total_tokens") == 0
    assert resp.tool_calls == []
    assert "tools" not in captured["kwargs"]  # 无工具则省略
    assert "response_format" not in captured["kwargs"]


def test_capabilities_passthrough() -> None:
    caps = _caps()
    adapter = oc.OpenAICompatAdapter(
        base_url="https://api.fake.test/v1", api_key="sk-x", capabilities=caps
    )
    assert adapter.capabilities() is caps


def _schema_request() -> InferRequest:
    return InferRequest(
        model_id="glm-x",
        messages=[{"role": "user", "content": "出题"}],
        structured_output={"name": "P", "schema": {"type": "object"}},
    )


async def test_structured_output_falls_back_to_json_object() -> None:
    """DeepSeek 等不支持 json_schema 时：降级 json_object + schema 进提示。"""
    import httpx
    import openai

    calls: list[dict] = []

    def make_client():
        async def create(**kwargs):
            calls.append(kwargs)
            if kwargs.get("response_format", {}).get("type") == "json_schema":
                resp = httpx.Response(400, request=httpx.Request("POST", "https://x"))
                raise openai.BadRequestError(
                    "This response_format type is unavailable now", response=resp, body=None
                )
            return _response(content='{"p": 1}')

        return NS(chat=NS(completions=NS(create=create)))

    import agent_adapters.openai_compat as oc

    orig = oc.AsyncOpenAI
    oc.AsyncOpenAI = lambda **kw: make_client()  # type: ignore[assignment]
    try:
        adapter = oc.OpenAICompatAdapter(
            base_url="https://api.fake.test/v1", api_key="sk-x", capabilities=_caps()
        )
        resp = await adapter.infer(_schema_request())
        assert resp.text == '{"p": 1}'
        assert len(calls) == 2
        assert calls[1]["response_format"] == {"type": "json_object"}
        assert "json_schema" in str(calls[1]["messages"][-1]["content"]).lower() or "schema" in str(
            calls[1]["messages"][-1]["content"]
        ).lower()
        # 模式缓存：后续调用直接走 json_object，不再先撞 400
        await adapter.infer(_schema_request())
        assert len(calls) == 3
    finally:
        oc.AsyncOpenAI = orig  # type: ignore[assignment]

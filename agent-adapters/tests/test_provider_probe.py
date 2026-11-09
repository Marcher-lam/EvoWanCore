"""T2 供应商探测：全部 mock 传输，不发真实网络请求。

隔离规则：每个用例只放行被测探测，其余两个探测打桩为 unavailable。
"""

import json
from pathlib import Path

import agent_adapters.provider_probe as pp
import httpx
import pytest
from agent_adapters.provider_probe import (
    ProbeConfig,
    ProbeReport,
    ProbeResult,
    load_config_from_env,
    probe_providers,
    write_report,
)


def _config() -> ProbeConfig:
    return ProbeConfig(
        base_url="https://api.fake.test/v1",
        api_key="sk-test",
        model_chat="chat-x",
        model_vlm="vlm-x",
        model_realtime="rt-x",
    )


async def _stub(cfg: ProbeConfig) -> ProbeResult:
    return ProbeResult(available=False, detail="stubbed")


def _isolate(monkeypatch: pytest.MonkeyPatch, keep: str) -> None:
    for name in ("_probe_chat", "_probe_vlm", "_probe_realtime"):
        if name != keep:
            monkeypatch.setattr(pp, name, _stub)


def _patch_http(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    orig = httpx.AsyncClient

    def factory(**kw):
        kw.pop("transport", None)
        return orig(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _ok_completion(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}}
    )


@pytest.mark.asyncio
async def test_chat_probe_success(monkeypatch) -> None:
    _patch_http(monkeypatch, _ok_completion)
    _isolate(monkeypatch, "_probe_chat")
    report = await probe_providers(_config())
    assert isinstance(report, ProbeReport)
    assert report.chat.available is True
    assert "chat-x" in report.chat.detail
    assert report.vlm.detail == "stubbed"  # 未测探测被隔离


@pytest.mark.asyncio
async def test_chat_probe_failure_records_status(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    _patch_http(monkeypatch, handler)
    _isolate(monkeypatch, "_probe_chat")
    report = await probe_providers(_config())
    assert report.chat.available is False
    assert "401" in report.chat.detail


@pytest.mark.asyncio
async def test_vlm_probe_sends_image_and_succeeds(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "a chart"}}]})

    _patch_http(monkeypatch, handler)
    _isolate(monkeypatch, "_probe_vlm")
    report = await probe_providers(_config())
    assert report.vlm.available is True
    messages = captured["payload"]["messages"]
    image_parts = [
        p
        for m in messages
        for p in m.get("content", [])
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert image_parts, "VLM 探测必须携带 image_url 内容块"


@pytest.mark.asyncio
async def test_realtime_probe_handshake(monkeypatch) -> None:
    opened: list[str] = []

    class _FakeWS:
        async def close(self) -> None:
            opened.append("closed")

    async def fake_connect(url: str, **kw):
        opened.append(url)
        return _FakeWS()

    monkeypatch.setattr(pp.websockets, "connect", fake_connect)
    _isolate(monkeypatch, "_probe_realtime")
    report = await probe_providers(_config())
    assert report.realtime.available is True
    assert opened[0].startswith("wss://")
    assert opened[-1] == "closed"


@pytest.mark.asyncio
async def test_realtime_probe_failure(monkeypatch) -> None:
    async def boom(url: str, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(pp.websockets, "connect", boom)
    _isolate(monkeypatch, "_probe_realtime")
    report = await probe_providers(_config())
    assert report.realtime.available is False
    assert "connection refused" in report.realtime.detail


def test_missing_env_config_is_none_fields(monkeypatch) -> None:
    for var in (
        "EWC_BASE_URL",
        "EWC_API_KEY",
        "EWC_MODEL_CHAT",
        "EWC_MODEL_VLM",
        "EWC_MODEL_REALTIME",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config_from_env()
    assert cfg.base_url is None
    assert cfg.api_key is None


def test_write_report_roundtrip(tmp_path: Path) -> None:
    report = ProbeReport(
        chat={"available": True, "detail": "ok"},
        vlm={"available": False, "detail": "404"},
        realtime={"available": False, "detail": "refused"},
    )
    path = tmp_path / "probe.json"
    write_report(report, path)
    data = json.loads(path.read_text())
    assert set(data) == {"chat", "vlm", "realtime", "generated_at"}

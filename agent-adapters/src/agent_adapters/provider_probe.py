"""供应商可用性探测 — kickoff 核实（spec §9.1）。

单测全部 mock 传输；真实请求仅在显式运行 scripts/provider_probe.py 时发生。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets
from pydantic import BaseModel

# 1x1 红色像素 PNG
_TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
    "z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)


class ProbeConfig(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model_chat: str | None = None
    model_vlm: str | None = None
    model_realtime: str | None = None


class ProbeResult(BaseModel):
    available: bool
    detail: str


class ProbeReport(BaseModel):
    chat: ProbeResult
    vlm: ProbeResult
    realtime: ProbeResult
    generated_at: str = ""


def load_config_from_env() -> ProbeConfig:
    def env(name: str) -> str | None:
        val = os.environ.get(name)
        return val if val else None

    return ProbeConfig(
        base_url=env("EWC_BASE_URL"),
        api_key=env("EWC_API_KEY"),
        model_chat=env("EWC_MODEL_CHAT"),
        model_vlm=env("EWC_MODEL_VLM"),
        model_realtime=env("EWC_MODEL_REALTIME"),
    )


def _headers(config: ProbeConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {config.api_key}"}


def _missing(name: str) -> ProbeResult:
    return ProbeResult(available=False, detail=f"missing env {name}")


async def _probe_chat(config: ProbeConfig) -> ProbeResult:
    if not (config.base_url and config.api_key and config.model_chat):
        return _missing("EWC_BASE_URL / EWC_API_KEY / EWC_MODEL_CHAT")
    payload = {
        "model": config.model_chat,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.base_url.rstrip('/')}/chat/completions",
                headers=_headers(config),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", "?")
            return ProbeResult(
                available=True, detail=f"{config.model_chat}: responded ({tokens} tokens)"
            )
    except httpx.HTTPStatusError as e:
        return ProbeResult(
            available=False, detail=f"{config.model_chat}: HTTP {e.response.status_code}"
        )
    except Exception as e:  # noqa: BLE001 — 探测的产物就是失败详情
        return ProbeResult(
            available=False, detail=f"{config.model_chat}: {type(e).__name__}: {e}"
        )


async def _probe_vlm(config: ProbeConfig) -> ProbeResult:
    if not (config.base_url and config.api_key and config.model_vlm):
        return _missing("EWC_BASE_URL / EWC_API_KEY / EWC_MODEL_VLM")
    payload = {
        "model": config.model_vlm,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in one word."},
                    {"type": "image_url", "image_url": {"url": _TINY_PNG}},
                ],
            }
        ],
        "max_tokens": 16,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.base_url.rstrip('/')}/chat/completions",
                headers=_headers(config),
                json=payload,
            )
            resp.raise_for_status()
            return ProbeResult(available=True, detail=f"{config.model_vlm}: vision ok")
    except httpx.HTTPStatusError as e:
        return ProbeResult(
            available=False, detail=f"{config.model_vlm}: HTTP {e.response.status_code}"
        )
    except Exception as e:  # noqa: BLE001
        return ProbeResult(
            available=False, detail=f"{config.model_vlm}: {type(e).__name__}: {e}"
        )


def _realtime_url(config: ProbeConfig) -> str:
    base = config.base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base}/realtime?model={config.model_realtime}"


async def _probe_realtime(config: ProbeConfig) -> ProbeResult:
    if not (config.base_url and config.api_key and config.model_realtime):
        return _missing("EWC_BASE_URL / EWC_API_KEY / EWC_MODEL_REALTIME")
    url = _realtime_url(config)
    try:
        ws = await websockets.connect(url, additional_headers=_headers(config), open_timeout=15)
        await ws.close()
        return ProbeResult(available=True, detail=f"{config.model_realtime}: handshake ok ({url})")
    except Exception as e:  # noqa: BLE001
        return ProbeResult(
            available=False, detail=f"{config.model_realtime}: {type(e).__name__}: {e} ({url})"
        )


async def probe_providers(config: ProbeConfig) -> ProbeReport:
    chat, vlm, realtime = await asyncio.gather(
        _probe_chat(config), _probe_vlm(config), _probe_realtime(config)
    )
    return ProbeReport(
        chat=chat,
        vlm=vlm,
        realtime=realtime,
        generated_at=datetime.now(UTC).isoformat(),
    )


def write_report(report: ProbeReport, path: Path | str = "reports/provider_probe.json") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))

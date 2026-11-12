"""V8-T3 Playwright 真 CUA adapter：真浏览器 observe/act（无浏览器环境 skip）。"""

import base64

import pytest

pytest.importorskip("playwright", reason="playwright 未安装（extra: browser）")

from agent_adapters.playwright_env import BrowserUnavailable, PlaywrightEnvironmentAdapter  # noqa: E402

# 最小交互页面：点击按钮追加文本；输入框接受文本
_PAGE = (
    "data:text/html,<html><body>"
    "<button id='btn' onclick=\"document.getElementById('out').textContent='clicked!'\">"
    "Click me</button>"
    "<div id='out'>idle</div>"
    "<input id='inp' type='text'/>"
    "</body></html>"
)


async def _make_adapter() -> PlaywrightEnvironmentAdapter:
    adapter = PlaywrightEnvironmentAdapter()
    try:
        await adapter.goto(_PAGE)
    except BrowserUnavailable as e:  # CI 无 chromium 二进制
        pytest.skip(f"chromium 不可用: {e}")
    return adapter


async def test_adapter_conforms_to_reserved_protocol() -> None:
    from agent_core.reserved import EnvironmentAdapter

    adapter: EnvironmentAdapter = PlaywrightEnvironmentAdapter()
    assert callable(adapter.observe)
    assert callable(adapter.act)
    assert adapter.supports_snapshot() is True
    await adapter.aclose()


async def test_observe_returns_dom_and_screenshot() -> None:
    adapter = await _make_adapter()
    try:
        obs = await adapter.observe()
        assert "Click me" in obs["dom"], "DOM 快照必须含可读文本"
        assert obs["screenshot_url"].startswith("data:image/png;base64,")
        payload = obs["screenshot_url"].split(",", 1)[1]
        assert base64.b64decode(payload)[:4] == b"\x89PNG", "必须是真 PNG"
    finally:
        await adapter.aclose()


async def test_act_click_changes_dom() -> None:
    """真点击 → 页面状态变化反映到下一次 observe。"""
    adapter = await _make_adapter()
    try:
        r = await adapter.act({"action": "click", "target": "#btn"})
        assert r["ok"] is True
        obs = await adapter.observe()
        assert "clicked!" in obs["dom"], "点击效果必须可见"
    finally:
        await adapter.aclose()


async def test_act_type_returns_value() -> None:
    adapter = await _make_adapter()
    try:
        r = await adapter.act({"action": "type", "target": "#inp", "text": "hello"})
        assert r["ok"] is True
        assert r["value"] == "hello", "输入值必须真实落进 input"
    finally:
        await adapter.aclose()


async def test_act_scroll_and_screenshot() -> None:
    adapter = await _make_adapter()
    try:
        r1 = await adapter.act({"action": "scroll", "target": "body",
                                "direction": "down"})
        assert r1["ok"] is True
        r2 = await adapter.act({"action": "screenshot"})
        assert r2["ok"] is True and r2["url"].startswith("data:image/png;base64,")
    finally:
        await adapter.aclose()


async def test_act_unknown_action_reports_error() -> None:
    adapter = await _make_adapter()
    try:
        r = await adapter.act({"action": "teleport", "target": "#btn"})
        assert r["ok"] is False and "teleport" in r["error"]
    finally:
        await adapter.aclose()

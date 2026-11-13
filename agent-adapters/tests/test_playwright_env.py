"""V8-T3 Playwright 真 CUA adapter：真浏览器 observe/act（无浏览器环境 skip）。"""

import base64

import pytest
from aiohttp import web

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

# ---------- V9-T3: 真站点适配（iframe / shadow DOM / networkidle） ----------

_HOST_PAGE = """<html><body>
<h1>Host Page</h1>
<div id="shadow-host"></div>
<iframe id="fra" src="/frame"></iframe>
<script>
  const root = document.getElementById('shadow-host').attachShadow({mode:'open'});
  const b = document.createElement('button');
  b.id = 'sbtn'; b.textContent = 'ShadowBtn';
  b.onclick = () => { b.textContent = 'shadow-done'; };
  root.appendChild(b);
</script>
</body></html>"""

_FRAME_PAGE = """<html><body>
<button id="fbtn">FrameBtn</button>
<script>
  document.getElementById('fbtn').onclick = () => {
    document.getElementById('fout').textContent = 'frame-clicked';
  };
</script>
<div id="fout">frame-idle</div>
</body></html>"""

# 懒加载页：300ms 后 fetch 才把文本放进来（networkidle 等待的靶子）
_SLOW_PAGE = """<html><body><div id="late">loading</div>
<script>
setTimeout(() => fetch('/late-data').then(r => r.text()).then(t => {
  document.getElementById('late').textContent = t;
}), 300);
</script></body></html>"""


async def _site():
    """起真 HTTP 站点（host + frame + slow 三页）。返回 (runner, url)。"""
    async def _host(_r):
        return web.Response(text=_HOST_PAGE, content_type="text/html")

    async def _frame(_r):
        return web.Response(text=_FRAME_PAGE, content_type="text/html")

    async def _slow(_r):
        return web.Response(text=_SLOW_PAGE, content_type="text/html")

    async def _late(_r):
        return web.Response(text="late-content-arrived")

    app = web.Application()
    app.router.add_get("/host", _host)
    app.router.add_get("/frame", _frame)
    app.router.add_get("/slow", _slow)
    app.router.add_get("/late-data", _late)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, f"http://127.0.0.1:{port}"


async def test_iframe_click_and_observe() -> None:
    """iframe 内按钮：target 未命中主 frame → 遍历子 frame 命中并真点击。"""
    runner, url = await _site()
    adapter = PlaywrightEnvironmentAdapter()
    try:
        await adapter.goto(f"{url}/host")
        # 主 frame DOM（含 shadow 内文本由 inner_text 不可见——见 shadow 测试）
        obs = await adapter.observe()
        assert "Host Page" in obs["dom"]
        assert "frame-idle" in obs["dom"], "observe 必须并入 iframe 文本"
        r = await adapter.act({"action": "click", "target": "#fbtn"})
        assert r["ok"] is True, f"iframe 内点击失败: {r}"
        obs2 = await adapter.observe()
        assert "frame-clicked" in obs2["dom"], "iframe 点击效果必须可见"
    finally:
        await adapter.aclose()
        await runner.cleanup()


async def test_open_shadow_dom_click() -> None:
    """open shadow root 内按钮：Playwright CSS 原生穿透可点击。"""
    runner, url = await _site()
    adapter = PlaywrightEnvironmentAdapter()
    try:
        await adapter.goto(f"{url}/host")
        r = await adapter.act({"action": "click", "target": "#sbtn"})
        assert r["ok"] is True, f"shadow 点击失败: {r}"
        obs = await adapter.observe()
        assert "shadow-done" in obs["dom"], "shadow 内状态变化必须可见"
    finally:
        await adapter.aclose()
        await runner.cleanup()


async def test_observe_networkidle_waits_for_lazy_content() -> None:
    """observe(settle='networkidle')：等到懒加载请求落定后的文本。"""
    runner, url = await _site()
    adapter = PlaywrightEnvironmentAdapter()
    try:
        await adapter.goto(f"{url}/slow")
        obs = await adapter.observe(settle="networkidle")
        assert "late-content-arrived" in obs["dom"], \
            "networkidle 必须等到懒加载完成"
    finally:
        await adapter.aclose()
        await runner.cleanup()


async def test_target_not_found_reports_error() -> None:
    """目标在所有 frame 均未命中 → 如实报错（ok=False）。"""
    runner, url = await _site()
    adapter = PlaywrightEnvironmentAdapter()
    try:
        await adapter.goto(f"{url}/host")
        r = await adapter.act({"action": "click", "target": "#nope-anywhere"})
        assert r["ok"] is False and "nope-anywhere" in r["error"]
    finally:
        await adapter.aclose()
        await runner.cleanup()

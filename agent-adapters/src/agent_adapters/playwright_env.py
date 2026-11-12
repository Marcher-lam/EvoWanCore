"""Playwright 真 CUA adapter（V8-T3）——实现 EnvironmentAdapter Protocol。

真 chromium（headless）：observe = 可读性 DOM 文本 + PNG 截图（data URL）；
act = click/type/scroll/screenshot 真执行。浏览器按需懒启动；
无浏览器环境抛 BrowserUnavailable（调用方决定 skip 还是失败）。
依赖可选 extra：`uv add agent-adapters --extra browser`。
"""

from __future__ import annotations

from typing import Any


class BrowserUnavailable(RuntimeError):
    """chromium 无法启动（二进制缺失/系统不支持）——CI 环境应 skip。"""


class PlaywrightEnvironmentAdapter:
    """实现 reserved.EnvironmentAdapter Protocol：真浏览器 observe/act。"""

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None

    def supports_snapshot(self) -> bool:
        return True

    async def _ensure(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserUnavailable(f"playwright 未安装: {e}") from e
        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self._headless)
            context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
            self._page = await context.new_page()
        except Exception as e:  # noqa: BLE001 — 启动失败统一为不可用
            await self.aclose()
            raise BrowserUnavailable(f"chromium 启动失败: {e}") from e

    async def goto(self, url: str) -> None:
        """导航到目标页面（CUA 会话的入口动作）。"""
        await self._ensure()
        await self._page.goto(url)

    async def observe(self) -> dict:
        """DOM 快照（可读性文本）+ PNG 截图 data URL。"""
        await self._ensure()
        dom: str = await self._page.inner_text("body")
        png: bytes = await self._page.screenshot()
        import base64

        return {
            "dom": dom,
            "screenshot_url": (
                "data:image/png;base64," + base64.b64encode(png).decode()
            ),
        }

    async def act(self, action: dict) -> dict:
        """真执行：click/type/scroll/screenshot。"""
        await self._ensure()
        kind = action.get("action", "")
        target = action.get("target", "")
        try:
            if kind == "click":
                await self._page.click(target)
                return {"ok": True, "clicked": target}
            if kind == "type":
                await self._page.fill(target, action.get("text", ""))
                value = await self._page.input_value(target)
                return {"ok": True, "typed": action.get("text", ""), "value": value}
            if kind == "scroll":
                dy = 400 if action.get("direction", "down") == "down" else -400
                await self._page.mouse.wheel(0, dy)
                return {"ok": True}
            if kind == "screenshot":
                import base64

                png: bytes = await self._page.screenshot()
                return {
                    "ok": True,
                    "url": "data:image/png;base64,"
                    + base64.b64encode(png).decode(),
                }
            return {"ok": False, "error": f"unknown action: {kind}"}
        except Exception as e:  # noqa: BLE001 — 执行失败如实返回，不炸会话
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def aclose(self) -> None:
        """释放浏览器资源（幂等）。"""
        for attr, closer in (
            ("_page", "close"), ("_browser", "close"), ("_pw", "stop"),
        ):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    await getattr(obj, closer)()
                except Exception:  # noqa: BLE001 — 清理路径不抛
                    pass
                setattr(self, attr, None)

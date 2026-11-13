"""Playwright 真 CUA adapter（V8-T3）——实现 EnvironmentAdapter Protocol。

真 chromium（headless）：observe = 可读性 DOM 文本 + PNG 截图（data URL）；
act = click/type/scroll/screenshot 真执行。浏览器按需懒启动；
无浏览器环境抛 BrowserUnavailable（调用方决定 skip 还是失败）。
依赖可选 extra：`uv add agent-adapters --extra browser`。
"""

from __future__ import annotations

import asyncio
import base64
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
        self._lock = asyncio.Lock()  # 懒启动双检（防并发双起浏览器）

    def supports_snapshot(self) -> bool:
        return True

    async def _ensure(self) -> None:
        if self._page is not None:
            return
        async with self._lock:
            if self._page is not None:  # 双检：并发者已启动
                return
            await self._start()

    async def _start(self) -> None:
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

    _SHADOW_TEXT_JS = """
    () => {
      const out = [];
      for (const el of document.querySelectorAll('*')) {
        if (el.shadowRoot) {
          // ShadowRoot 是 DocumentFragment——没有 innerText，用 textContent
          const t = el.shadowRoot.textContent || '';
          if (t.trim()) out.push(t.trim());
        }
      }
      return out.join('\\n');
    }
    """

    async def _dom_text(self) -> str:
        """主 + 子 frame + open shadow 树的可读性文本。"""
        chunks: list[str] = []
        for frame in self._page.frames:
            try:
                text: str = await frame.inner_text("body")
            except Exception:  # noqa: BLE001 — 无 body / 未就绪 frame → 跳过
                continue
            if text.strip():
                chunks.append(text.strip())
            try:
                shadow: str = await frame.evaluate(self._SHADOW_TEXT_JS)
            except Exception:  # noqa: BLE001 — evaluate 不可用 → 跳过
                continue
            if shadow.strip():
                chunks.append(shadow.strip())
        return "\n".join(chunks)

    async def _resolve(self, target: str):
        """目标解析：主 frame 优先，未命中遍历子 frame（iframe 内元素可定位）。

        open shadow DOM 由 Playwright CSS 引擎原生穿透，无需特判。
        返回 Locator 或 None（全部 frame 未命中）。
        """
        main = self._page.locator(target)
        if await main.count() > 0:
            return main
        for frame in self._page.frames:
            if frame is self._page.main_frame:
                continue
            loc = frame.locator(target)
            if await loc.count() > 0:
                return loc
        return None

    async def observe(self, settle: str | None = None) -> dict:
        """DOM 快照（主+子 frame 可读性文本）+ PNG 截图 data URL。

        settle：可选等待——"networkidle" 等网络静默（懒加载站点），
        None（默认）不额外等待。
        """
        await self._ensure()
        if settle == "networkidle":
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001 — 超时降级：拿当前状态
                pass
        dom = await self._dom_text()
        png: bytes = await self._page.screenshot()
        return {
            "dom": dom,
            "screenshot_url": (
                "data:image/png;base64," + base64.b64encode(png).decode()
            ),
        }

    async def act(self, action: dict) -> dict:
        """真执行：click/type/scroll/screenshot（目标穿透 iframe）。"""
        await self._ensure()
        kind = action.get("action", "")
        target = action.get("target", "")
        try:
            if kind in ("click", "type"):
                loc = await self._resolve(target)
                if loc is None:
                    return {
                        "ok": False,
                        "error": f"target not found in any frame: {target}",
                    }
                if kind == "click":
                    await loc.click()
                    return {"ok": True, "clicked": target}
                await loc.fill(action.get("text", ""))
                value = await loc.input_value()
                return {"ok": True, "typed": action.get("text", ""), "value": value}
            if kind == "scroll":
                # scroll 相对当前视口（target 参数仅作语义提示，不参与定位）
                dy = 400 if action.get("direction", "down") == "down" else -400
                await self._page.mouse.wheel(0, dy)
                return {"ok": True}
            if kind == "screenshot":
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

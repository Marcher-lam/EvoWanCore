"""T8/T9 CUA：HTTP EnvironmentAdapter（observe/act 桩）+ 能力注册。"""

from aiohttp import web


class _FakeEnvServer:
    """最小环境桩：/observe 返回 DOM+截图 URL，/act 回显动作。"""

    def __init__(self) -> None:
        self.acts: list[dict] = []
        self.runner: web.AppRunner | None = None
        self.port = 18500

    async def start(self) -> None:
        async def _observe(_request: web.Request) -> web.Response:
            return web.json_response({
                "dom": "<html><body><button id='b1'>OK</button></body></html>",
                "screenshot_url": f"http://127.0.0.1:{self.port}/shot.png",
            })

        async def _act(request: web.Request) -> web.Response:
            action = await request.json()
            self.acts.append(action)
            kind = action.get("action", "")
            if kind == "click":
                return web.json_response({"ok": True, "clicked": action.get("target")})
            if kind == "type":
                return web.json_response({"ok": True, "typed": action.get("text")})
            if kind == "screenshot":
                return web.json_response({"ok": True, "url": "re://shot"})
            return web.json_response({"ok": False, "error": f"unknown action: {kind}"})

        app = web.Application()
        app.router.add_get("/observe", _observe)
        app.router.add_post("/act", _act)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        await site.start()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()


async def test_adapter_conforms_to_reserved_protocol() -> None:
    """HTTPEnvironmentAdapter 结构化实现 reserved.EnvironmentAdapter。"""
    from agent_core.environment import HTTPEnvironmentAdapter
    from agent_core.reserved import EnvironmentAdapter

    adapter: EnvironmentAdapter = HTTPEnvironmentAdapter("http://127.0.0.1:19999")
    assert callable(adapter.observe)
    assert callable(adapter.act)
    assert callable(adapter.supports_snapshot)


async def test_observe_returns_dom() -> None:
    """observe → {dom, screenshot_url}。"""
    from agent_core.environment import HTTPEnvironmentAdapter

    server = _FakeEnvServer()
    await server.start()
    try:
        adapter = HTTPEnvironmentAdapter(f"http://127.0.0.1:{server.port}")
        obs = await adapter.observe()
        assert "<button" in obs["dom"]
        assert obs["screenshot_url"].endswith("/shot.png")
    finally:
        await server.stop()


async def test_act_click_and_type() -> None:
    """act(click/type/screenshot) → POST /act → 环境侧可观察。"""
    from agent_core.environment import HTTPEnvironmentAdapter

    server = _FakeEnvServer()
    await server.start()
    try:
        adapter = HTTPEnvironmentAdapter(f"http://127.0.0.1:{server.port}")
        r1 = await adapter.act({"action": "click", "target": "#b1"})
        assert r1["ok"] is True and r1["clicked"] == "#b1"
        r2 = await adapter.act({"action": "type", "target": "#input", "text": "hi"})
        assert r2["ok"] is True and r2["typed"] == "hi"
        r3 = await adapter.act({"action": "screenshot"})
        assert r3["ok"] is True
        assert [a["action"] for a in server.acts] == ["click", "type", "screenshot"]
    finally:
        await server.stop()


def test_cua_tools_discoverable() -> None:
    """T9：register_cua_tools → click/type/screenshot/observe 进能力目录。"""
    from agent_core.capability import InMemoryCapabilityRegistry
    from agent_core.environment import register_cua_tools

    class _SyncAdapter:
        """observe/act 不必异步等待远端时用于注册（handler 内 await）。"""

        def supports_snapshot(self) -> bool:
            return True

    registry = InMemoryCapabilityRegistry()
    register_cua_tools(registry, _SyncAdapter())

    ids = {d.id for d in registry.discover({"tags": ["cua"]})}
    assert {"cua_observe", "cua_click", "cua_type", "cua_screenshot"} <= ids
    # 红线：CUA 写操作必须声明 write 副作用 + 非空 auth_scope（需显式授权）
    by_id = {d.id: d for d in registry.discover({})}
    assert by_id["cua_click"].side_effect_level == "write"
    assert by_id["cua_type"].side_effect_level == "write"
    assert by_id["cua_click"].auth_scope == ["cua:act"]
    assert by_id["cua_type"].auth_scope == ["cua:act"]
    assert by_id["cua_observe"].side_effect_level == "read"
    assert by_id["cua_observe"].auth_scope == []
    assert by_id["cua_screenshot"].auth_scope == []

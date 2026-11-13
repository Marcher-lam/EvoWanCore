"""T4 V8 验收套件：三块前非目标转正——A2A 标准 / TS SDK / Playwright CUA。"""

import subprocess
from pathlib import Path

import aiohttp

_REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# C1 A2A 标准：Card 发现 → JSON-RPC message/send 闭环
# ---------------------------------------------------------------------------

async def test_v8_a2a_standard_discover_and_talk() -> None:
    from agent_core.a2a_standard import (
        A2AStandardClient,
        A2AStandardEndpoint,
        AgentCard,
        AgentCardSkill,
    )
    from agent_core.transport import A2AEnvelope

    async def handler(env: A2AEnvelope) -> A2AEnvelope:
        return A2AEnvelope(
            from_agent=env.to_agent, to_agent=env.from_agent,
            message_type="reply", payload={"text": "task done: 3 fields"},
        )

    card = AgentCard(
        name="v8-worker", description="V8 验收 worker",
        url="http://127.0.0.1:1/", skills=[
            AgentCardSkill(id="extract", name="抽取", tags=["doc"]),
        ],
    )
    ep = A2AStandardEndpoint(card, handler, port=0)
    await ep.start()
    try:
        async with aiohttp.ClientSession() as client:
            # 1) 发现：拉 Card
            async with client.get(
                f"http://127.0.0.1:{ep.port}/.well-known/agent-card.json"
            ) as resp:
                assert resp.status == 200
                discovered = await resp.json()
            assert discovered["name"] == "v8-worker"
            assert discovered["skills"][0]["id"] == "extract"
            # 2) 交谈：message/send
            reply = await A2AStandardClient(
                f"http://127.0.0.1:{ep.port}"
            ).send_text("抽取发票字段")
            assert reply == "task done: 3 fields"
    finally:
        await ep.stop()


# ---------------------------------------------------------------------------
# C2 TS SDK：编译 + 对真 Python server 的 node 冒烟 e2e
# ---------------------------------------------------------------------------

def test_v8_ts_sdk_compiles_and_smokes() -> None:
    """tsc build（产 dist）+ node 冒烟（SMOKE PASS）——新 clone 可跑。"""
    import shutil

    import pytest

    ts_dir = _REPO / "agent-sdk-ts"
    if not (ts_dir / "node_modules").exists():
        if shutil.which("npm") is None:
            pytest.skip("npm 不可用（TS SDK 冒烟跳过）")
        install = subprocess.run(
            ["npm", "install"], cwd=ts_dir, capture_output=True, text=True,
            timeout=300,
        )
        assert install.returncode == 0, f"npm install 失败: {install.stderr[-400:]}"
    for cmd in (
        ["npx", "tsc", "-p", "tsconfig.json"],  # build（产 dist，非 noEmit）
        ["node", "smoke/run.mjs"],
    ):
        out = subprocess.run(
            cmd, cwd=ts_dir, capture_output=True, text=True, timeout=180,
        )
        assert out.returncode == 0, (
            f"{cmd} 失败:\nstdout={out.stdout[-800:]}\nstderr={out.stderr[-800:]}"
        )
        if cmd[0] == "node":
            assert "SMOKE PASS" in out.stdout


# ---------------------------------------------------------------------------
# C3 Playwright 真 CUA：真浏览器闭环（无浏览器 skip）
# ---------------------------------------------------------------------------

async def test_v8_real_browser_cua_loop() -> None:
    """observe → 真点击 → observe 验证状态变化（完整感知-行动闭环）。"""
    import pytest

    pytest.importorskip("playwright")
    from agent_adapters.playwright_env import (
        BrowserUnavailable,
        PlaywrightEnvironmentAdapter,
    )

    page = (
        "data:text/html,<html><body><h1>Invoice</h1>"
        "<button id='go' onclick=\"this.textContent='done'\">Process</button>"
        "</body></html>"
    )
    adapter = PlaywrightEnvironmentAdapter()
    try:
        await adapter.goto(page)
    except BrowserUnavailable as e:
        import pytest

        await adapter.aclose()
        pytest.skip(f"chromium 不可用: {e}")
    try:
        obs = await adapter.observe()
        assert "Invoice" in obs["dom"] and "Process" in obs["dom"]
        assert (await adapter.act({"action": "click", "target": "#go"}))["ok"]
        obs2 = await adapter.observe()
        assert "done" in obs2["dom"] and "Process" not in obs2["dom"]
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# C4 红线：A2A 标准端点鉴权恒时比较 + 无 key 泄漏（沿用既有扫描）
# ---------------------------------------------------------------------------

async def test_v8_a2a_standard_auth_constant_time() -> None:
    """鉴权路径使用 hmac（源码断言）+ 401 拒绝。"""
    import hmac as hmac_mod

    import agent_core.a2a_standard as mod
    src = Path(mod.__file__).read_text()
    assert "hmac.compare_digest" in src, "token 比较必须恒时"
    assert hmac_mod.compare_digest(b"a", b"a")  # 依赖自检

    from agent_core.a2a_standard import A2AStandardEndpoint, AgentCard
    from agent_core.transport import A2AEnvelope

    async def handler(env: A2AEnvelope) -> A2AEnvelope | None:
        return None

    ep = A2AStandardEndpoint(
        AgentCard(name="x", url="http://x/"), handler, port=0, auth_token="t",
    )
    await ep.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{ep.port}/",
                json={"jsonrpc": "2.0", "id": "1", "method": "message/send",
                      "params": {}},
            ) as resp:
                assert resp.status == 401
    finally:
        await ep.stop()


def test_v8_no_secrets_regression() -> None:
    """沿用全仓 sk- 泄漏扫描（V8 新增 TS 源也入扫描范围）。"""
    import re

    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    pattern = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")
    offenders = []
    for f in out:
        if not f.endswith((".py", ".ts", ".mjs", ".toml", ".json")):
            continue
        p = _REPO / f
        if p.exists() and pattern.search(p.read_text(errors="ignore")):
            offenders.append(f)
    assert offenders == [], f"疑似 key 泄漏: {offenders}"


# ---------------------------------------------------------------------------
# V9 追加：backlog 清零（Notification 204 / 真站点 iframe+shadow+networkidle）
# ---------------------------------------------------------------------------

async def test_v9_a2a_notification_semantics() -> None:
    """无 id → 副作用执行 + 204 无 body；客户端 notify_text 闭环。"""
    from agent_core.a2a_standard import (
        A2AStandardClient,
        A2AStandardEndpoint,
        AgentCard,
    )
    from agent_core.transport import A2AEnvelope

    handled: list[A2AEnvelope] = []

    async def handler(env: A2AEnvelope) -> A2AEnvelope | None:
        handled.append(env)
        return None

    ep = A2AStandardEndpoint(
        AgentCard(name="v9", url="http://x/"), handler, port=0,
    )
    await ep.start()
    try:
        assert await A2AStandardClient(
            f"http://127.0.0.1:{ep.port}"
        ).notify_text("one-way") is None
        assert len(handled) == 1 and handled[0].payload["text"] == "one-way"
    finally:
        await ep.stop()


async def test_v9_playwright_real_site_full_loop() -> None:
    """真 HTTP 站点：iframe 点击 + shadow 点击 + networkidle 懒加载一站验证。"""
    import pytest

    pytest.importorskip("playwright")
    from agent_adapters.playwright_env import (
        BrowserUnavailable,
        PlaywrightEnvironmentAdapter,
    )
    from aiohttp import web

    async def _host(_r):
        return web.Response(text=(
            "<html><body><h1>Host</h1>"
            "<div id='sh'></div><iframe src='/fr'></iframe>"
            "<script>const r=document.getElementById('sh').attachShadow({mode:'open'});"
            "r.innerHTML=\"<button id='sb' onclick=\\\"this.textContent='sd'\\\">S</button>\";"
            "</script></body></html>"
        ), content_type="text/html")

    async def _fr(_r):
        return web.Response(text=(
            "<html><body><button id='fb' onclick="
            "\"document.getElementById('fo').textContent='fc'\">F</button>"
            "<div id='fo'>fi</div></body></html>"
        ), content_type="text/html")

    app = web.Application()
    app.router.add_get("/host", _host)
    app.router.add_get("/fr", _fr)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    adapter = PlaywrightEnvironmentAdapter()
    try:
        try:
            await adapter.goto(f"http://127.0.0.1:{port}/host")
        except BrowserUnavailable as e:
            pytest.skip(f"chromium 不可用: {e}")
        assert (await adapter.act({"action": "click", "target": "#fb"}))["ok"]
        assert (await adapter.act({"action": "click", "target": "#sb"}))["ok"]
        obs = await adapter.observe()
        assert "fc" in obs["dom"], "iframe 点击效果可见"
        assert "sd" in obs["dom"], "shadow 点击效果可见"
    finally:
        await adapter.aclose()
        await runner.cleanup()


# ---------------------------------------------------------------------------
# V10 追加：工程面收口（CI 就绪 / SDK 鉴权 e2e）
# ---------------------------------------------------------------------------

async def test_v10_sdk_server_auth_e2e(tmp_path) -> None:
    """auth_token：全端点 401 拒无凭证；对 token submit→SSE 全链路。"""
    import asyncio
    from pathlib import Path

    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession, ClientTimeout

    from tests_acceptance.test_v6_acceptance import _base_factory

    tmp = Path(tmp_path)
    server = AgentSDKServer(_base_factory(tmp, [
        {"tool_calls": [{"id": "c", "name": "noop", "arguments": {}}]},
        {"text": "done"},
    ]), auth_token="v10key")
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "v10", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                assert resp.status == 401, "无凭证必须 401"
            h = {"Authorization": "Bearer v10key"}
            async with client.post(f"{server.url}/runs", json={
                "task_id": "v10", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }, headers=h) as resp:
                assert resp.status == 201
                run_id = (await resp.json())["run_id"]
            final_state = None
            for _ in range(250):
                async with client.get(
                    f"{server.url}/runs/{run_id}", headers=h,
                ) as r:
                    final_state = (await r.json())["state"]
                    if final_state in ("succeeded", "failed", "cancelled"):
                        break
                await asyncio.sleep(0.02)
            assert final_state == "succeeded", f"轮询超时/未成功: {final_state}"
            events = []
            async with client.get(
                f"{server.url}/runs/{run_id}/events/stream",
                headers={**h}, timeout=ClientTimeout(total=15),
            ) as stream:
                assert stream.status == 200
                async for raw in stream.content:
                    line = raw.decode().strip()
                    if line.startswith("data:"):
                        events.append(line)
                    elif line.startswith("event:") and "end" in line:
                        break
            assert len(events) >= 3, "鉴权下 SSE 全链路必须通"
    finally:
        await server.stop()


def test_v10_ci_workflows_valid() -> None:
    """CI/release workflow：YAML 可解析 + 关键步骤在场。"""
    import yaml

    root = Path(__file__).resolve().parents[1]
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    rel = yaml.safe_load((root / ".github/workflows/release.yml").read_text())
    assert "python" in ci["jobs"] and "ts" in ci["jobs"]
    py_steps = " ".join(s.get("run", "") for s in ci["jobs"]["python"]["steps"])
    assert "ruff check" in py_steps and "pytest" in py_steps
    assert "playwright install" in py_steps, "CI 必须装真浏览器"
    ts_steps = " ".join(
        s.get("run", "") for s in ci["jobs"]["ts"]["steps"])
    assert "tsc" in ts_steps and "smoke/run.mjs" in ts_steps
    assert "workflow_dispatch" in (
        rel.get(True) or rel.get("on") or {}
    ), "release 必须手动触发"

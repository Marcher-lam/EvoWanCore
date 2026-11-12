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

async def test_v8_ts_sdk_compiles_and_smokes() -> None:
    """tsc --noEmit 过 + node 冒烟（SMOKE PASS）。"""
    ts_dir = _REPO / "agent-sdk-ts"
    if not (ts_dir / "node_modules").exists():
        pytest_skip_msg = "node_modules 未安装（npm install 后再跑）"
        compile_check = subprocess.run(
            ["npx", "--yes", "typescript@5", "tsc", "--version"],
            cwd=ts_dir, capture_output=True,
        )
        if compile_check.returncode != 0:
            import pytest

            pytest.skip(pytest_skip_msg)
    for cmd in (
        ["npx", "tsc", "-p", "tsconfig.json", "--noEmit"],
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

"""T12 V5 验收套件：生态层全栈——SDK/编排/A2A/CUA/记忆清除/Realtime 接入点。"""

import asyncio
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# A1 服务化：SDK HTTP API 端到端（真端口、真内核 runtime）
# ---------------------------------------------------------------------------

def _sdk_app_factory(tmp_path: Path):
    """最小可运行 app 工厂（真内核 runtime + FakeModel）。"""
    from agent_core.capability import (
        CapabilityDescriptor,
        CapabilityType,
        InMemoryCapabilityRegistry,
    )
    from agent_core.evidence import SqliteEvidenceStore
    from agent_core.models import (
        FakeModelAdapter,
        InferResponse,
        Modality,
        ModelCapabilities,
        ModelGateway,
    )
    from agent_core.policy import DefaultPolicyEngine
    from agent_core.runtime.driver import AgentSpec, VerificationResult
    from agent_core.runtime.eventlog import SqliteEventLog
    from agent_core.runtime.runtime import AgentRuntime
    from agent_core.runtime.statestore import SqliteStateStore

    class _OKVerifier:
        async def verify_action(self, request, result):
            return VerificationResult(passed=True)

        async def verify_task(self, task, evidence_refs):
            return VerificationResult(passed=True)

    def factory():
        registry = InMemoryCapabilityRegistry()

        async def handler(args):
            return {"ok": True}

        registry.bind(
            CapabilityDescriptor(
                id="noop", type=CapabilityType.TOOL, name="noop",
                input_schema={}, side_effect_level="read", auth_scope=[],
            ), handler)
        fake = FakeModelAdapter(
            ModelCapabilities(
                model_id="m-v5", inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            [
                InferResponse(tool_calls=[{"id": "c", "name": "noop", "arguments": {}}]),
                InferResponse(text="done"),
            ],
        )
        gateway = ModelGateway()
        gateway.register(fake)
        runtime = AgentRuntime(
            gateway=gateway, registry=registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=SqliteEventLog(tmp_path / "acc_log.db"),
            state_store=SqliteStateStore(tmp_path / "acc_state.db"),
            evidence_store=SqliteEvidenceStore(
                tmp_path / "acc_ev.db", tmp_path / "acc_blobs"),
            verifier=_OKVerifier(),
        )
        agent = AgentSpec(id="default", role="worker", model_id="m-v5")
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    return factory


async def test_v5_sdk_http_roundtrip(tmp_path: Path) -> None:
    """POST /runs → 201 QUEUED；轮询 → succeeded；GET events ≥3。"""
    import asyncio

    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_sdk_app_factory(tmp_path), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "v5-acc", "kind": "test", "goal": {"what": "noop"},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                assert resp.status == 201
                body = await resp.json()
                run_id = body["run_id"]
                assert body["state"] == "queued", "提交-轮询：立即返回 QUEUED"
            # 轮询到终态
            for _ in range(250):
                async with client.get(f"{server.url}/runs/{run_id}") as r:
                    if (await r.json())["state"] == "succeeded":
                        break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("run 未到 succeeded")
            async with client.get(f"{server.url}/runs/{run_id}/events") as resp:
                body = await resp.json()
                assert body["count"] >= 3
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# A2 编排：能力匹配 + 并行执行
# ---------------------------------------------------------------------------

async def test_v5_orchestration_policy_and_parallel(tmp_path: Path) -> None:
    """CapabilityCompositionPolicy 选人 + Orchestrator 真并行执行到终态。"""
    from agent_core.capability import (
        CapabilityDescriptor,
        CapabilityType,
        InMemoryCapabilityRegistry,
    )
    from agent_core.evidence import SqliteEvidenceStore
    from agent_core.models import (
        FakeModelAdapter,
        InferResponse,
        Modality,
        ModelCapabilities,
        ModelGateway,
    )
    from agent_core.orchestration import CapabilityCompositionPolicy, Orchestrator
    from agent_core.policy import DefaultPolicyEngine
    from agent_core.runtime.driver import AgentSpec, VerificationResult
    from agent_core.runtime.eventlog import SqliteEventLog
    from agent_core.runtime.runtime import AgentRuntime
    from agent_core.runtime.statestore import SqliteStateStore
    from agent_core.task import TaskSpec

    class _OKVerifier:
        async def verify_action(self, request, result):
            return VerificationResult(passed=True)

        async def verify_task(self, task, evidence_refs):
            return VerificationResult(passed=True)

    def _runtime(tag: str) -> AgentRuntime:
        registry = InMemoryCapabilityRegistry()

        async def handler(args):
            return {"ok": True}

        registry.bind(
            CapabilityDescriptor(
                id="noop", type=CapabilityType.TOOL, name="noop",
                input_schema={}, side_effect_level="read", auth_scope=[],
            ), handler)
        fake = FakeModelAdapter(
            ModelCapabilities(
                model_id=f"m-{tag}", inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            [
                InferResponse(tool_calls=[{"id": "c", "name": "noop", "arguments": {}}]),
                InferResponse(text="done"),
            ],
        )
        gateway = ModelGateway()
        gateway.register(fake)
        return AgentRuntime(
            gateway=gateway, registry=registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=SqliteEventLog(tmp_path / f"o{tag}_log.db"),
            state_store=SqliteStateStore(tmp_path / f"o{tag}_state.db"),
            evidence_store=SqliteEvidenceStore(
                tmp_path / f"o{tag}_ev.db", tmp_path / f"o{tag}_blobs"),
            verifier=_OKVerifier(),
        )

    agents = [
        AgentSpec(id="w_a", role="worker", capabilities=["extract"], model_id="m-a"),
        AgentSpec(id="w_b", role="worker", capabilities=["extract"], model_id="m-b"),
        AgentSpec(id="w_c", role="worker", capabilities=["export"], model_id="m-c"),
    ]
    orchestrator = Orchestrator(
        CapabilityCompositionPolicy(),
        {"w_a": _runtime("a"), "w_b": _runtime("b"), "w_c": _runtime("c")},
    )
    task = TaskSpec(
        id="v5_orch", kind="extract", goal={"what": "parallel"},
        input_schema={}, constraints={}, acceptance_criteria=[{"check": "done"}],
    )
    results = await orchestrator.orchestrate(task, agents)
    assert set(results) == {"w_a", "w_b"}, "只选 extract 能力者"
    assert all(s == "succeeded" for s in results.values())


# ---------------------------------------------------------------------------
# A3 A2A：信封 + 注册表 + HTTP 往返
# ---------------------------------------------------------------------------

async def test_v5_a2a_end_to_end() -> None:
    """注册表发现 → transport.send → 远端 handler → reply。"""
    from agent_core.transport import (
        A2AEnvelope,
        AgentRegistry,
        HTTPAgentTransport,
        InboundA2AHandler,
    )

    async def handler(env: A2AEnvelope):
        return A2AEnvelope(
            from_agent=env.to_agent, to_agent=env.from_agent,
            message_type="task_result", payload={"done": True},
            reply_to=env.message_id,
        )

    inbound = InboundA2AHandler(port=0, handler=handler)
    await inbound.start()
    try:
        registry = AgentRegistry()
        registry.register("worker", ["extract"], f"http://127.0.0.1:{inbound.port}")
        found = registry.discover("extract")
        assert len(found) == 1

        transport = HTTPAgentTransport(found[0]["endpoint"])
        await transport.send(A2AEnvelope(
            from_agent="orchestrator", to_agent="worker",
            message_type="task_request", payload={"task": "extract"},
        ))
        reply = await asyncio.wait_for(transport.receive(), timeout=5)
        assert reply["message_type"] == "task_result"
        assert reply["payload"] == {"done": True}
    finally:
        await inbound.stop()


# ---------------------------------------------------------------------------
# A4 CUA：observe/act + 能力红线
# ---------------------------------------------------------------------------

async def test_v5_cua_adapter_and_redlines() -> None:
    """observe 返回 DOM；act 可点击；能力目录副作用声明如实（红线）。"""
    from agent_core.capability import InMemoryCapabilityRegistry
    from agent_core.environment import HTTPEnvironmentAdapter, register_cua_tools
    from aiohttp import web

    async def _observe(_r):
        return web.json_response({"dom": "<html>hi</html>", "screenshot_url": "s://1"})

    async def _act(r):
        a = await r.json()
        return web.json_response({"ok": True, "action": a["action"]})

    app = web.Application()
    app.router.add_get("/observe", _observe)
    app.router.add_post("/act", _act)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)  # 动态端口
    await site.start()
    env_port = runner.addresses[0][1]
    try:
        adapter = HTTPEnvironmentAdapter(f"http://127.0.0.1:{env_port}")
        obs = await adapter.observe()
        assert "dom" in obs and "screenshot_url" in obs
        assert (await adapter.act({"action": "click", "target": "#x"}))["ok"]

        registry = InMemoryCapabilityRegistry()
        register_cua_tools(registry, adapter)
        by_id = {d.id: d for d in registry.discover({})}
        # 红线：写操作如实声明 write + 需显式授权；读操作免授权
        assert by_id["cua_click"].side_effect_level == "write"
        assert by_id["cua_type"].side_effect_level == "write"
        assert by_id["cua_click"].auth_scope == ["cua:act"]
        assert by_id["cua_type"].auth_scope == ["cua:act"]
        assert by_id["cua_observe"].auth_scope == []
        assert by_id["cua_screenshot"].auth_scope == []
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# A5 记忆物理清除 + 审计
# ---------------------------------------------------------------------------

async def test_v5_memory_purge_physical_and_audited(tmp_path: Path) -> None:
    """全版本物理 DELETE（非 tombstone）+ 审计事件可查。"""
    import aiosqlite
    from agent_core.memory.purge import SqlitePurgeAuditor, purge_memory
    from agent_core.memory.record import MemoryRecord
    from agent_core.memory.store import NS_RULES, SqliteMemoryStore

    store = SqliteMemoryStore(tmp_path / "acc.db")
    rid = "mem_acc_1"
    for _ in range(3):
        await store.put(MemoryRecord(
            id=rid, namespace=NS_RULES, kind="rule", content={"r": "x"},
        ))
    auditor = SqlitePurgeAuditor(tmp_path / "acc_audit.db")
    audit = await purge_memory(store, auditor, NS_RULES, rid, reason="acceptance")
    assert audit.deleted_revisions == 3
    assert await store.get(rid) is None
    async with aiosqlite.connect(tmp_path / "acc.db") as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id = ?", (rid,)
        )
        assert (await cur.fetchone())[0] == 0
    assert len(await auditor.list_events(rid)) == 1


# ---------------------------------------------------------------------------
# A6 Realtime 接入点：key 缺失明确报错；绝不硬编码
# ---------------------------------------------------------------------------

def test_v5_realtime_key_entry(monkeypatch) -> None:
    from agent_adapters.realtime_openai import OpenAIRealtimeAdapter

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIRealtimeAdapter()


def test_v5_no_secrets_in_tracked_files() -> None:
    """红线：被跟踪源码/测试中不得出现真实 key 形态（sk- 长串）。"""
    import re

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    pattern = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")
    offenders = []
    for f in out:
        if not f.endswith((".py", ".toml", ".md", ".yml", ".yaml", ".json")):
            continue
        p = root / f
        if not p.exists():
            continue
        if pattern.search(p.read_text(errors="ignore")):
            offenders.append(f)
    assert offenders == [], f"疑似真实 key 泄漏: {offenders}"

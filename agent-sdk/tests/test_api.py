"""T1 SDK HTTP API：submit(QUEUED→poll) → get → events → cancel/explore/purge。"""

import asyncio
from pathlib import Path


def _make_app_factory(tmp_path: Path):
    """最小可运行 app 工厂——不依赖工作台（纯内核）。"""
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
                model_id="m-sdk", inputs={Modality.TEXT}, tools={"native_tool_call"}
            ),
            [
                InferResponse(tool_calls=[
                    {"id": "c1", "name": "noop", "arguments": {}}
                ]),
                InferResponse(text="done"),
            ],
        )
        gateway = ModelGateway()
        gateway.register(fake)
        runtime = AgentRuntime(
            gateway=gateway, registry=registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=SqliteEventLog(tmp_path / "log.db"),
            state_store=SqliteStateStore(tmp_path / "state.db"),
            evidence_store=SqliteEvidenceStore(tmp_path / "ev.db", tmp_path / "blobs"),
            verifier=_OKVerifier(),
        )
        agent = AgentSpec(id="default", role="worker", model_id="m-sdk")
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    return factory


async def _wait_terminal(client, url: str, run_id: str, timeout_s: float = 5.0):
    """轮询直到终态（提交-轮询语义）。"""
    for _ in range(int(timeout_s / 0.02)):
        async with client.get(f"{url}/runs/{run_id}") as resp:
            body = await resp.json()
            if body["state"] in ("succeeded", "failed", "cancelled"):
                return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} 未在 {timeout_s}s 内到终态")


async def test_submit_queued_then_terminal(tmp_path: Path) -> None:
    """POST /runs → 201 QUEUED + run_id；轮询 → succeeded；事件 ≥3。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "t1", "kind": "test", "goal": {"what": "noop"},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                assert resp.status == 201
                body = await resp.json()
                run_id = body["run_id"]
                assert body["state"] == "queued"

            final = await _wait_terminal(client, server.url, run_id)
            assert final["state"] == "succeeded"

            async with client.get(f"{server.url}/runs/{run_id}/events") as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["count"] >= 3, "至少 RunCreated+Action+RunVerified"
                types = [e["type"] for e in body["events"]]
                assert "RunCreated" in types
                assert "RunVerified" in types

            async with client.get(f"{server.url}/runs/nonexistent") as resp:
                assert resp.status == 404
    finally:
        await server.stop()


async def test_agent_routing_no_silent_fallback(tmp_path: Path) -> None:
    """未知 agent_id → 404（不静默回退到首个 agent）；非法 JSON → 400。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs", json={
                "task_id": "t2", "kind": "test", "goal": {}, "agent_id": "ghost",
            }) as resp:
                assert resp.status == 404
                assert "ghost" in (await resp.json())["error"]

            async with client.post(
                f"{server.url}/runs", data="{not json",
                headers={"Content-Type": "application/json"},
            ) as resp:
                assert resp.status == 400
    finally:
        await server.stop()


async def test_cancel_unknown_run_404_and_idempotent(tmp_path: Path) -> None:
    """cancel 未知 run → 404；已知 run → 200 且 cancelled 如实。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/runs/nonexistent/cancel") as resp:
                assert resp.status == 404

            async with client.post(f"{server.url}/runs", json={
                "task_id": "t3", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                run_id = (await resp.json())["run_id"]
            await _wait_terminal(client, server.url, run_id)
            # 幂等 cancel：已终态 run 的 cancel 返回 200 + cancelled 如实（False）
            async with client.post(f"{server.url}/runs/{run_id}/cancel") as resp:
                assert resp.status == 200
                assert (await resp.json())["cancelled"] is False
    finally:
        await server.stop()


async def test_explore_requires_app_503(tmp_path: Path) -> None:
    """工厂未提供 app → 503（而非 AttributeError→500）。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path), port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/explore", json={}) as resp:
                assert resp.status == 503
    finally:
        await server.stop()


async def test_memory_purge_endpoint(tmp_path: Path) -> None:
    """DELETE /memory/{ns}/{id} → 物理删除 + 审计；未知 → 404；未配置 → 503。"""
    from agent_core.memory.purge import SqlitePurgeAuditor
    from agent_core.memory.record import MemoryRecord
    from agent_core.memory.store import NS_RULES, SqliteMemoryStore
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    base = _make_app_factory(tmp_path)
    store = SqliteMemoryStore(tmp_path / "mem.db")
    auditor = SqlitePurgeAuditor(tmp_path / "audit.db")

    def factory():
        components = base()
        components["memory_store"] = store
        components["purge_auditor"] = auditor
        return components

    await store.put(MemoryRecord(
        id="mem_ep_1", namespace=NS_RULES, kind="rule", content={"r": 1},
    ))
    server = AgentSDKServer(factory, port=0)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.delete(
                f"{server.url}/memory/{NS_RULES}/mem_ep_1?reason=test"
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["deleted_revisions"] == 1
                assert body["reason"] == "test"
            assert await store.get("mem_ep_1") is None
            assert len(await auditor.list_events()) == 1

            async with client.delete(
                f"{server.url}/memory/{NS_RULES}/mem_ep_1"
            ) as resp:
                assert resp.status == 404
    finally:
        await server.stop()

    # 未配置 memory 组件 → 503
    server2 = AgentSDKServer(_make_app_factory(tmp_path / "x"), port=0)
    await server2.start()
    try:
        from aiohttp import ClientSession as CS

        async with CS() as client:
            async with client.delete(f"{server2.url}/memory/rules/x") as resp:
                assert resp.status == 503
    finally:
        await server2.stop()


# ---------- V10-T2: SDK server bearer 鉴权 ----------

async def test_sdk_auth_rejects_missing_or_wrong_token(tmp_path: Path) -> None:
    """auth_token 设置：无凭证/错凭证 → 401 + WWW-Authenticate。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path), auth_token="sdkkey")
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.get(f"{server.url}/runs/x") as resp:
                assert resp.status == 401
                assert resp.headers.get("WWW-Authenticate") == "Bearer"
            async with client.get(
                f"{server.url}/runs/x", headers={"Authorization": "Bearer wrong"},
            ) as resp:
                assert resp.status == 401
            # 鉴权在路由之外：无凭证打不存在路径 → 401（而非 404 泄漏路由面）
            async with client.get(f"{server.url}/definitely/not/a/route") as resp:
                assert resp.status == 401
    finally:
        await server.stop()


async def test_sdk_auth_all_endpoints_with_token(tmp_path: Path) -> None:
    """对 token：submit→轮询→events 全链路畅通。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path), auth_token="sdkkey")
    await server.start()
    try:
        async with ClientSession() as client:
            h = {"Authorization": "Bearer sdkkey"}
            async with client.post(f"{server.url}/runs", json={
                "task_id": "auth1", "kind": "test", "goal": {},
                "acceptance_criteria": [{"check": "done"}],
            }, headers=h) as resp:
                assert resp.status == 201
                run_id = (await resp.json())["run_id"]
            for _ in range(250):
                async with client.get(
                    f"{server.url}/runs/{run_id}", headers=h,
                ) as r:
                    if (await r.json())["state"] == "succeeded":
                        break
                await asyncio.sleep(0.02)
            async with client.get(
                f"{server.url}/runs/{run_id}/events", headers=h,
            ) as resp:
                assert (await resp.json())["count"] >= 3
    finally:
        await server.stop()


async def test_sdk_auth_default_open_unchanged(tmp_path: Path) -> None:
    """默认 auth_token=None：开放（回环兼容，既有测试即证明——冒烟一条）。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path))
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.get(f"{server.url}/runs/none") as resp:
                assert resp.status == 404, "无鉴权时按业务语义（404）而非 401"
    finally:
        await server.stop()


async def test_sdk_auth_token_misconfig_fail_fast() -> None:
    """构造期 fail-fast：空串与非 latin-1 token（否则全站静默恒 401）。"""
    import pytest
    from agent_sdk.api import AgentSDKServer

    factory = _make_app_factory(Path("/tmp/never-used"))
    with pytest.raises(ValueError, match="空串"):
        AgentSDKServer(factory, auth_token="")
    with pytest.raises(ValueError, match="latin-1"):
        AgentSDKServer(factory, auth_token="密钥不能是中文")

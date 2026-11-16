"""V13-T2 SDK 通用扩展点：extra_routes（二次开发应用挂自有路由）。

平台原则：SDK 不知道任何具体应用；应用路由挂进同一 app →
自动享受鉴权/400 守卫中间件。
"""

from pathlib import Path

from agent_sdk.api import AgentSDKServer
from aiohttp import ClientSession, web


def _demo_factory(tmp_path: Path):
    """带 runtime 的最小工厂（/runs 路由需要 runtime 组件）。"""
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

    base = tmp_path

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
                model_id="m-extra", inputs={Modality.TEXT},
                tools={"native_tool_call"},
            ),
            [InferResponse(text="done")],
        )
        gateway = ModelGateway()
        gateway.register(fake)
        runtime = AgentRuntime(
            gateway=gateway, registry=registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=SqliteEventLog(base / "log.db"),
            state_store=SqliteStateStore(base / "state.db"),
            evidence_store=SqliteEvidenceStore(
                base / "ev.db", base / "blobs"),
            verifier=_OKVerifier(),
        )
        agent = AgentSpec(id="default", role="worker", model_id="m-extra")
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    return factory


async def test_extra_routes_mounted_and_authed(tmp_path: Path) -> None:
    """应用路由（demo /hello）挂载可用；auth_token 下同受鉴权保护。"""
    routes = web.RouteTableDef()

    @routes.get("/hello")
    async def _hello(_request: web.Request) -> web.Response:
        return web.json_response({"msg": "hi"})

    server = AgentSDKServer(_demo_factory(tmp_path), auth_token="k1",
                            extra_routes=routes)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.get(f"{server.url}/hello") as resp:
                assert resp.status == 401, "应用路由必须同受平台鉴权"
            async with client.get(
                f"{server.url}/hello", headers={"Authorization": "Bearer k1"},
            ) as resp:
                assert resp.status == 200
                assert (await resp.json())["msg"] == "hi"
    finally:
        await server.stop()


async def test_extra_routes_default_none_unchanged(tmp_path: Path) -> None:
    """不传 extra_routes：SDK 行为完全不变（向后兼容）。"""
    server = AgentSDKServer(_demo_factory(tmp_path))
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.get(f"{server.url}/runs/x") as resp:
                assert resp.status == 404  # 业务语义（无鉴权模式）
    finally:
        await server.stop()


async def test_extra_routes_bad_request_guard(tmp_path: Path) -> None:
    """应用路由抛 ValidationError/JSONDecodeError → 平台 400 守卫兜住。"""
    routes = web.RouteTableDef()

    @routes.post("/echo")
    async def _echo(request: web.Request) -> web.Response:
        from agent_sdk.models import SubmitRunRequest

        body = SubmitRunRequest(**await request.json())
        return web.json_response(body.model_dump())

    server = AgentSDKServer(_demo_factory(tmp_path), extra_routes=routes)
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(
                f"{server.url}/echo", data="{bad",
                headers={"Content-Type": "application/json"},
            ) as resp:
                assert resp.status == 400, "应用路由吃平台 400 守卫"
    finally:
        await server.stop()

"""T1 SDK HTTP API：submit → get → events → cancel 端到端。"""

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


async def test_submit_and_get_run(tmp_path: Path) -> None:
    """POST /runs → GET /runs/{id} → 终态 + 事件。"""
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(_make_app_factory(tmp_path), port=18300)
    await server.start()
    try:
        async with ClientSession() as client:
            # 提交
            async with client.post(f"{server.url}/runs", json={
                "task_id": "t1", "kind": "test", "goal": {"what": "noop"},
                "acceptance_criteria": [{"check": "done"}],
            }) as resp:
                assert resp.status == 201
                body = await resp.json()
                run_id = body["run_id"]
                assert body["state"] in ("succeeded", "failed")

            # 查询
            async with client.get(f"{server.url}/runs/{run_id}") as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["state"] == "succeeded"

            # 事件
            async with client.get(f"{server.url}/runs/{run_id}/events") as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["count"] >= 3, "至少 RunCreated+Action+RunVerified"
                types = [e["type"] for e in body["events"]]
                assert "RunCreated" in types
                assert "RunVerified" in types

            # 404
            async with client.get(f"{server.url}/runs/nonexistent") as resp:
                assert resp.status == 404
    finally:
        await server.stop()

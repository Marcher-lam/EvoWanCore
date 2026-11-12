"""TS SDK 冒烟服务端：最小工厂起 AgentSDKServer，stdout 打印 READY <url>。"""

import asyncio
import sys
import tempfile
from pathlib import Path


async def main() -> None:
    from agent_sdk.api import AgentSDKServer

    tmp = Path(tempfile.mkdtemp(prefix="ts-smoke-"))

    def factory():
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
                model_id="m-ts", inputs={Modality.TEXT},
                tools={"native_tool_call"},
            ),
            [
                InferResponse(
                    tool_calls=[{"id": "c", "name": "noop", "arguments": {}}]
                ),
                InferResponse(text="done"),
            ],
        )
        gateway = ModelGateway()
        gateway.register(fake)
        runtime = AgentRuntime(
            gateway=gateway, registry=registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=SqliteEventLog(tmp / "log.db"),
            state_store=SqliteStateStore(tmp / "state.db"),
            evidence_store=SqliteEvidenceStore(tmp / "ev.db", tmp / "blobs"),
            verifier=_OKVerifier(),
        )
        agent = AgentSpec(id="default", role="worker", model_id="m-ts")
        return {"runtime": runtime, "agents": {"default": agent}, "app": None}

    server = AgentSDKServer(factory, port=0)  # 动态端口
    await server.start()
    print(f"READY {server.url}", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

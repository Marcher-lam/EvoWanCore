"""TS SDK 冒烟服务端：最小工厂起 AgentSDKServer，stdout 打印 READY <url>。"""

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ts-smoke-"))


async def main() -> None:
    from agent_sdk.api import AgentSDKServer

    tmp = _TMP

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
        components = {
            "runtime": runtime, "agents": {"default": agent}, "app": None,
        }
        # MRAG 组件（V12 冒烟）：Fake VLM + Hash 向量——零网络
        from examples.mrag.app import MRAGApp
        from examples.mrag.embed import HashEmbedding

        class _SmokeVLM:
            def capabilities(self):
                from agent_core.models import (
                    Modality,
                    ModelCapabilities,
                )

                return ModelCapabilities(
                    model_id="m-smoke-vlm",
                    inputs={Modality.TEXT, Modality.IMAGE},
                )

            async def infer(self, request):
                from agent_core.models import InferResponse

                return InferResponse(text="金额 42 元 [smoke.pdf p.1]。")

        mrag = MRAGApp(
            db_path=tmp / "mrag.db", blobs_dir=tmp / "mrag_blobs",
            embedder=HashEmbedding(), vlm=_SmokeVLM(),
        )
        components["mrag"] = mrag
        return components

    # 冒烟默认启用鉴权——顺带覆盖 TS authToken 全路径（OCR test 项）
    server = AgentSDKServer(
        factory, port=0, auth_token=os.environ.get("SMOKE_AUTH_TOKEN"),
    )
    await server.start()
    # MRAG 预热（async 上下文内；READY 前完成——客户端打来时索引就绪）
    mrag = server._components.get("mrag")  # noqa: SLF001 — smoke server 内部
    if mrag is not None and not (tmp / "mrag_seeded").exists():
        (tmp / "mrag_seeded").write_text("1")
        from examples.mrag.store import SourceDoc as _SD

        await mrag.ingest(_SD(
            name="smoke.pdf",
            pages=[(1, b"\x89PNG\r\n\x1anp1")],
            text="第1页：冒烟语料 金额 42 元。",
        ))
    print(f"READY {server.url}", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await server.stop()
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

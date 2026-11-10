"""T24b Omni 实时语音示例：会话中 tool_call 走已发布技能。"""

import asyncio
import json

from agent_adapters.realtime import RealtimeSessionAdapter
from agent_core.capability import CapabilityDescriptor, CapabilityType, InMemoryCapabilityRegistry
from agent_core.runtime.executor import CapabilityExecutor
from examples.docworkbench import tools as wb
from examples.docworkbench.generator import generate_corpus
from examples.omni.app import OmniApp, run_omni_session
from websockets.asyncio.server import serve


async def test_omni_tool_call_uses_published_skill() -> None:
    corpus = generate_corpus(seed=3, size=4, difficulty=0.3)
    doc = corpus.documents[0]
    tool_result_seen: list[dict] = []
    instructions_seen: list[str] = []

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "session.update":
                instructions_seen.append(msg.get("instructions", ""))
                await ws.send(
                    json.dumps(
                        {
                            "type": "tool_call",
                            "payload": {
                                "call_id": "c1",
                                "name": "convert_format",
                                "arguments": {"doc_id": doc["id"], "dpi": 200},
                            },
                        }
                    )
                )
            if msg["type"] == "tool_result":
                tool_result_seen.append(msg)
                await ws.send(json.dumps({"type": "closed", "payload": {}}))
            if msg["type"] == "close":
                await ws.close()

    server = await serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        # 已发布技能/记忆注入 instructions
        published_memory = "已验证规则：xlsx 导出含图表扫描页前必须 convert_format(dpi=200)"

        registry = InMemoryCapabilityRegistry()
        derived: dict[str, dict] = {}

        async def convert_handler(args):
            converted = wb.convert_format(
                next(d for d in corpus.documents if d["id"] == args["doc_id"]),
                dpi=args.get("dpi", 200),
            )
            derived[args["doc_id"]] = converted
            return {
                "lossy": converted.get("lossy", False),
                "dpi": converted.get("dpi"),
                "export_ready": converted.get("export_ready", False),
            }

        registry.bind(
            CapabilityDescriptor(
                id="convert_format",
                type=CapabilityType.TOOL,
                name="格式转换",
                input_schema={"doc_id": "str", "dpi": "int"},
                side_effect_level="write",
                auth_scope=[],
                tags=["workbench"],
            ),
            convert_handler,
        )
        executor = CapabilityExecutor(registry=registry, skill_runner=None)

        adapter = RealtimeSessionAdapter(
            endpoint=f"ws://127.0.0.1:{port}/v1/realtime", api_key="k"
        )
        omni = OmniApp(
            session_adapter=adapter,
            executor=executor,
            instructions=published_memory,
            model_id="rt-x",
        )
        result = await asyncio.wait_for(run_omni_session(omni), timeout=10)

        assert result.tool_results, "会话中应执行并回传工具结果"
        assert result.tool_results[0]["output"]["export_ready"] is True
        assert result.tool_results[0]["output"]["lossy"] is False
        assert all("convert_format" in i for i in instructions_seen)  # 技能引用进会话指令
    finally:
        server.close()
        await server.wait_closed()

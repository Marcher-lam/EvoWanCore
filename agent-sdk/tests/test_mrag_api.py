"""V12 MRAG 服务化：POST /mrag/query（answer/retrieve 双模式）。"""

from pathlib import Path


def _mrag_factory(tmp_path: Path):
    """带 MRAG 组件的最小工厂（Fake VLM/HashEmbedding，零网络）。"""
    from examples.mrag.app import MRAGApp
    from examples.mrag.embed import HashEmbedding

    class _EchoVLM:
        def __init__(self) -> None:
            from agent_core.models import Modality, ModelCapabilities

            self._caps = ModelCapabilities(
                model_id="m-api", inputs={Modality.TEXT, Modality.IMAGE},
            )
            self.calls = 0

        def capabilities(self):
            return self._caps

        async def infer(self, request):
            from agent_core.models import InferResponse

            self.calls += 1
            prompt = str(request.messages)
            import re

            m = re.search(r"\[1\] \((\S+) p\.(\d+)", prompt)
            fact = re.search(r"相关度 [\d.]+\)\s*([^\n]+)", prompt)
            doc, page = (m.group(1), m.group(2)) if m else ("?", "1")
            return InferResponse(
                text=f"{fact.group(1) if fact else '见材料'} [{doc} p.{page}]。"
            )

    from examples.mrag.store import SourceDoc

    def factory():
        app = MRAGApp(
            db_path=tmp_path / "api.db", blobs_dir=tmp_path / "api_blobs",
            embedder=HashEmbedding(), vlm=_EchoVLM(),
        )
        return {"mrag": app, "app": None}

    doc = SourceDoc(
        name="api-test.pdf",
        pages=[(1, b"\x89PNG\r\n\x1a\np1"), (2, b"\x89PNG\r\n\x1a\np2")],
        text="第1页：合同金额 9900 元。\n第2页：违约金按 10% 计。",
    )
    # 预热索引（factory 闭包外的 app 每次重建 → 预热放 fixtures 层）：
    def seeded_factory():
        return factory()

    return factory, doc


async def test_mrag_endpoint_no_component_503(tmp_path: Path) -> None:
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    server = AgentSDKServer(lambda: {"app": None})  # 无 mrag 组件
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "x",
            }) as resp:
                assert resp.status == 503
    finally:
        await server.stop()


async def test_mrag_retrieve_and_answer_modes(tmp_path: Path) -> None:
    from agent_sdk.api import AgentSDKServer
    from aiohttp import ClientSession

    factory, doc = _mrag_factory(tmp_path)

    async def seeded():
        components = factory()
        app = components["mrag"]

        n = await app.ingest(doc)  # 预热：描述块+文本块
        assert n >= 3
        return components

    # 预热必须发生在 server 持有的同一 app 实例上——先建再包
    components = await seeded()
    server = AgentSDKServer(lambda: components)
    await server.start()
    try:
        async with ClientSession() as client:
            # retrieve 模式：不走 VLM
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "违约金", "mode": "retrieve", "k": 2,
            }) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["answer"] == "", "retrieve 模式不生成答案"
                assert len(body["hits"]) == 2
                assert any("10%" in h["content"] for h in body["hits"])
                assert body["hits"][0]["page_image"], "命中须带页面图像文件名"

            # answer 模式：VLM 生成 + 引用
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "违约金", "mode": "answer", "k": 2,
            }) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["answer"], "answer 模式必须有答案"
                assert ("api-test.pdf", 2) in [
                    (c["doc"], c["page"]) for c in body["citations"]
                ], f"引用错误: {body['citations']}"

            # 非法 mode → 400（ValidationError 中间件）
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "x", "mode": "teleport",
            }) as resp:
                assert resp.status == 400
    finally:
        await server.stop()

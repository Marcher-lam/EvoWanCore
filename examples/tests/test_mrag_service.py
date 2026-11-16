"""V13 应用层 mrag 服务：extra_routes 挂载 + 平台鉴权/守卫全覆盖。"""

from pathlib import Path

from agent_sdk.api import AgentSDKServer
from aiohttp import ClientSession
from examples.mrag.embed import HashEmbedding


class _EchoVLM:
    """描述阶段按页给真内容；回答阶段复述首块 + 引用。"""

    _DESCS = ["合同首页：含金额条款", "质保页：质保期 3 年"]

    def __init__(self) -> None:
        from agent_core.models import Modality, ModelCapabilities

        self._caps = ModelCapabilities(
            model_id="m-svc", inputs={Modality.TEXT, Modality.IMAGE},
        )
        self.calls = 0

    def capabilities(self):
        return self._caps

    async def infer(self, request):
        import re

        from agent_core.models import InferResponse

        prompt = str(request.messages)
        m = re.search(r"\[1\] \((\S+) p\.(\d+)", prompt)
        if m:  # 回答阶段
            fact = re.search(r"相关度 [\d.]+\)\s*([^\n]+)", prompt)
            return InferResponse(
                text=f"{fact.group(1) if fact else '见材料'} "
                     f"[{m.group(1)} p.{m.group(2)}]。"
            )
        desc = self._DESCS[min(self.calls, len(self._DESCS) - 1)]
        self.calls += 1
        return InferResponse(text=desc)


async def _make_server(tmp_path: Path, auth_token: str | None = "k1"):
    from agent_sdk.api import AgentSDKServer
    from examples.mrag.app import MRAGApp
    from examples.mrag.service import build_mrag_routes
    from examples.mrag.store import SourceDoc

    mrag = MRAGApp(
        db_path=tmp_path / "svc.db", blobs_dir=tmp_path / "svc_blobs",
        embedder=HashEmbedding(), vlm=_EchoVLM(),
    )
    await mrag.ingest(SourceDoc(
        name="svc.pdf",
        pages=[(1, b"\x89PNG\r\n\x1a\np1"), (2, b"\x89PNG\r\n\x1a\np2")],
        text="第1页：合同金额 7700 元。\n第2页：质保期 3 年。",
    ))
    components = {"mrag": mrag, "app": None}
    from agent_sdk.api import SDK_COMPONENTS

    server = AgentSDKServer(
        lambda: components, auth_token=auth_token,
        extra_routes=build_mrag_routes(
            lambda request: request.app[SDK_COMPONENTS]["mrag"]
        ),
    )
    await server.start()
    return server


async def test_app_level_mrag_endpoint(tmp_path: Path) -> None:
    """应用路由挂平台：鉴权 401 → retrieve/answer 双模式 → 引用正确。"""
    server = await _make_server(tmp_path)
    try:
        async with ClientSession() as client:
            h = {"Authorization": "Bearer k1"}
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "质保",
            }) as resp:
                assert resp.status == 401, "应用路由吃平台鉴权"
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "质保", "mode": "retrieve", "k": 2,
            }, headers=h) as resp:
                body = await resp.json()
                assert body["answer"] == ""
                assert any("3 年" in hit["content"] for hit in body["hits"])
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "质保", "mode": "answer", "k": 2,
            }, headers=h) as resp:
                body = await resp.json()
                assert ("svc.pdf", 2) in [
                    (c["doc"], c["page"]) for c in body["citations"]
                ]
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "x", "mode": "teleport",
            }, headers=h) as resp:
                assert resp.status == 400
    finally:
        await server.stop()


_PLATFORM_PATHS = [
    "agent-core/src", "agent-sdk/src", "agent-adapters/src",
    "agent-explore/src", "agent-evolve/src", "agent-devtools/src",
    "agent-sdk-ts/src",  # TS SDK 同属平台
]


def test_platform_has_no_mrag_concept() -> None:
    """架构红线：平台包源码零 mrag 字样（cwd 锚定 + rc 语义，不可空过）。"""
    import subprocess

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "grep", "-il", "mrag", "--", *_PLATFORM_PATHS],
        capture_output=True, text=True, cwd=repo_root,
    )
    # rc=0 命中（fail）；rc=1 干净（pass）；其他（git 缺失等）显式报错不静默
    assert result.returncode != 0 or result.stdout.strip() == "", \
        f"平台包出现 mrag 概念: {result.stdout.strip()}"
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git grep 异常 rc={result.returncode}: {result.stderr}")


async def test_mrag_endpoint_503_when_component_missing(tmp_path: Path) -> None:
    """get_mrag 抛 KeyError（组件缺失）→ 应用路由 503。"""
    from examples.mrag.service import build_mrag_routes

    def _no_mrag(request):  # noqa: ARG001
        raise KeyError("mrag")

    server = AgentSDKServer(
        lambda: {"app": None},
        extra_routes=build_mrag_routes(_no_mrag),
    )
    await server.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"{server.url}/mrag/query", json={
                "query": "x",
            }) as resp:
                assert resp.status == 503
    finally:
        await server.stop()

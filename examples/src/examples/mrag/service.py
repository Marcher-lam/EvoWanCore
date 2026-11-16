"""MRAG 应用层 HTTP 服务（V13）——基于平台二次开发，不侵入 evowancore。

架构：AgentSDKServer(平台) 提供通用 extra_routes 扩展点 + 鉴权/400 守卫；
本模块（应用层）定义 mrag 自有路由挂进去。平台代码零 mrag 概念。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from aiohttp import web
from pydantic import BaseModel

Mode = Literal["answer", "retrieve"]


class MragQueryRequest(BaseModel):
    """MRAG 查询：answer=VLM 带引用生成（默认）；retrieve=仅检索。

    ValidationError 由平台 _bad_request_guard 统一转 400（应用不重复实现）。
    """

    query: str
    k: int = 4
    mode: Mode = "answer"


def build_mrag_routes(
    get_mrag: Callable[[web.Request], Any],
) -> web.RouteTableDef:
    """构造 mrag 路由表（挂进 AgentSDKServer(extra_routes=...)）。

    get_mrag(request)：从请求上下文取 MRAGApp——推荐
    `request.app["sdk_components"]["mrag"]`（平台组件注入机制）；
    取不到 raise KeyError → 本路由回 503（组件缺失属应用语义）。
    """
    routes = web.RouteTableDef()

    @routes.post("/mrag/query")
    async def mrag_query(request: web.Request) -> web.Response:
        body = MragQueryRequest(**await request.json())  # 校验异常→平台400守卫
        try:
            mrag = get_mrag(request)
        except KeyError:
            return web.json_response(
                {"error": "mrag not configured"}, status=503
            )
        retrieved = await mrag.retrieve(body.query, k=body.k)
        hits = [{
            "content": c.content, "doc": c.doc, "page": c.page,
            "score": round(c.score, 4),
            "page_image": c.page_image_path.rsplit("/", 1)[-1],
        } for c in retrieved]
        answer_text, citations = "", []
        if body.mode == "answer":
            result = await mrag.answer(body.query, k=body.k)
            answer_text = result.text
            citations = [{"doc": d, "page": p} for d, p in result.citations]
        return web.json_response({
            "answer": answer_text, "citations": citations, "hits": hits,
        })

    return routes

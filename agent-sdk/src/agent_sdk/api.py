"""agent-sdk HTTP API — aiohttp 服务器暴露内核操作（V5 T1 + 终评审修复）。

语义：
- POST /runs 立即返回 QUEUED（后台执行，提交-轮询模型，不阻塞 HTTP）
- GET /runs/{id} / GET /runs/{id}/events / POST /runs/{id}/cancel（幂等、如实）
- POST /explore 需要工厂提供 app 组件（缺失 → 503）
- DELETE /memory/{namespace}/{record_id} 需要工厂提供 memory_store +
  purge_auditor（缺失 → 503）；物理清除 + 审计
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from agent_sdk.models import (
    CancelRunResponse,
    EventResponse,
    ExploreResponse,
    PurgeResponse,
    ResumeRunRequest,
    RunStatusResponse,
    SubmitRunRequest,
)

AppFactory = Callable[[], dict[str, Any]]


@web.middleware
async def _bad_request_guard(request: web.Request, handler):
    """非法 JSON / 校验失败 → 400（而非 500）。"""
    try:
        return await handler(request)
    except (json.JSONDecodeError, ValidationError) as e:
        return web.json_response({"error": f"bad request: {e}"}, status=400)


class AgentSDKServer:
    """HTTP API 服务器——工厂函数返回含 runtime 等组件的字典。"""

    def __init__(self, app_factory: AppFactory, host: str = "127.0.0.1",
                 port: int = 8300, sse_poll_s: float = 0.05) -> None:
        self._factory = app_factory
        self._host = host
        self._port = port
        self._sse_poll_s = sse_poll_s
        self._components: dict[str, Any] = {}
        self._runner: web.AppRunner | None = None
        self._bg: set[asyncio.Task] = set()  # 强引用防 GC

    def _routes(self) -> web.RouteTableDef:
        routes = web.RouteTableDef()

        @routes.post("/runs")
        async def submit_run(request: web.Request) -> web.Response:
            body = SubmitRunRequest(**await request.json())
            from agent_core.task import TaskSpec

            runtime = self._components.get("runtime")
            if runtime is None:
                return web.json_response(
                    {"error": "runtime not configured"}, status=503
                )
            agents = self._components.get("agents", {})
            agent = agents.get(body.agent_id)
            if agent is None:
                # 空字典与未知 id 分开报（不静默回退到首个 agent）
                status = 503 if not agents else 404
                return web.json_response(
                    {"error": f"agent not found: {body.agent_id}"}, status=status
                )
            task = TaskSpec(
                id=body.task_id, kind=body.kind, goal=body.goal,
                input_schema=body.input_schema, constraints=body.constraints,
                acceptance_criteria=body.acceptance_criteria,
            )
            # 提交-轮询：同步建 run 落盘 QUEUED，执行转后台，立即返回 run_id
            run_id, bg = await runtime.submit_bg(task, agent)
            self._bg.add(bg)
            bg.add_done_callback(self._bg.discard)
            return web.json_response(
                RunStatusResponse(
                    run_id=run_id, state="queued", task_id=body.task_id
                ).model_dump(), status=201
            )

        @routes.get("/runs/{run_id}")
        async def get_run(request: web.Request) -> web.Response:
            run_id = request.match_info["run_id"]
            run = await self._components["runtime"].load_run(run_id)
            if run is None:
                return web.json_response({"error": "run not found"}, status=404)
            return web.json_response(
                RunStatusResponse(
                    run_id=run.id, state=run.state.value, task_id=run.task_id
                ).model_dump()
            )

        @routes.get("/runs/{run_id}/events")
        async def get_events(request: web.Request) -> web.Response:
            run_id = request.match_info["run_id"]
            runtime = self._components["runtime"]
            if await runtime.load_run(run_id) is None:
                return web.json_response({"error": "run not found"}, status=404)
            events = []
            async for e in runtime.events(run_id):
                events.append(EventResponse(
                    id=e.id, type=e.type, ts=e.ts.isoformat(),
                    subject_type=e.subject_type, subject_id=e.subject_id,
                    payload=e.payload,
                ).model_dump())
            return web.json_response({"events": events, "count": len(events)})

        @routes.get("/runs/{run_id}/events/stream")
        async def stream_events(request: web.Request) -> web.StreamResponse:
            """SSE：重放已有事件 → 增量推送 → 终态排空后 event: end 关闭。

            语义：背压依赖 StreamResponse.write 等待对端排空（有界写缓冲）；
            客户端断连 → write 抛 ConnectionError → 干净退出；
            Last-Event-ID 头 = 断线重连游标（只发该 id 之后的事件）。
            """
            run_id = request.match_info["run_id"]
            runtime = self._components["runtime"]
            if await runtime.load_run(run_id) is None:
                return web.json_response({"error": "run not found"}, status=404)
            last_event_id = request.headers.get("Last-Event-ID")

            resp = web.StreamResponse(headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            })
            await resp.prepare(request)

            sent_ids: set[str] = set()
            terminal = {"succeeded", "failed", "cancelled"}
            try:
                while True:
                    events = [e async for e in runtime.events(run_id)]
                    if last_event_id is not None and not sent_ids:
                        ids = [e.id for e in events]
                        if last_event_id in ids:
                            # 游标命中：断点及之前全部标记已发
                            for e in events[: ids.index(last_event_id) + 1]:
                                sent_ids.add(e.id)
                    emitted = 0
                    for e in events:
                        if e.id in sent_ids:
                            continue
                        payload = EventResponse(
                            id=e.id, type=e.type, ts=e.ts.isoformat(),
                            subject_type=e.subject_type, subject_id=e.subject_id,
                            payload=e.payload,
                        ).model_dump()
                        await resp.write(
                            (
                                f"id: {payload['id']}\n"
                                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            ).encode()
                        )
                        sent_ids.add(e.id)
                        emitted += 1
                    run = await runtime.load_run(run_id)
                    if (
                        run is not None
                        and run.state.value in terminal
                        and emitted == 0
                    ):
                        break  # 终态且已排空
                    await asyncio.sleep(self._sse_poll_s)
                await resp.write(b"event: end\ndata: {}\n\n")
                await resp.write_eof()
            except (ConnectionResetError, ConnectionError, RuntimeError):
                pass  # 客户端断连：干净退出，不吞服务端其他错误语义
            return resp

        @routes.post("/runs/{run_id}/resume")
        async def resume_run(request: web.Request) -> web.Response:
            """恢复非终态 run（crash/中断后）；image_urls 注入协议见 ResumeRunRequest。"""
            run_id = request.match_info["run_id"]
            runtime = self._components["runtime"]
            if await runtime.load_run(run_id) is None:
                return web.json_response({"error": "run not found"}, status=404)
            body = ResumeRunRequest(**await request.json())
            from agent_core.run import IllegalTransition

            try:
                run = await runtime.resume(
                    run_id,
                    image_urls=body.image_urls or None,
                )
            except IllegalTransition as e:
                return web.json_response(
                    {"error": f"run not resumable: {e}"}, status=409
                )
            return web.json_response(
                RunStatusResponse(
                    run_id=run.id, state=run.state.value, task_id=run.task_id
                ).model_dump()
            )

        @routes.post("/runs/{run_id}/cancel")
        async def cancel_run(request: web.Request) -> web.Response:
            run_id = request.match_info["run_id"]
            runtime = self._components["runtime"]
            run = await runtime.load_run(run_id)
            if run is None:
                return web.json_response({"error": "run not found"}, status=404)
            await runtime.cancel(run_id)
            after = await runtime.load_run(run_id)
            cancelled = after is not None and after.state.value == "cancelled"
            return web.json_response(
                CancelRunResponse(
                    run_id=run_id, cancelled=cancelled
                ).model_dump()
            )

        @routes.post("/explore")
        async def explore(request: web.Request) -> web.Response:
            from agent_sdk.models import ExploreRequest

            body = ExploreRequest(**await request.json())
            app = self._components.get("app")
            if app is None or not hasattr(app, "explore"):
                return web.json_response(
                    {"error": "explore app not configured"}, status=503
                )
            policy = _build_exploration_policy(body)
            report = await (
                app.explore(policy) if policy is not None else app.explore()
            )
            return web.json_response(ExploreResponse(
                waves_executed=report.waves_executed,
                tasks_executed=report.tasks_executed,
                tasks_passed=report.tasks_passed,
                candidates_generated=report.candidates_generated,
                budget_spent=report.budget_spent,
            ).model_dump())

        @routes.delete("/memory/{namespace}/{record_id}")
        async def purge_memory(request: web.Request) -> web.Response:
            ns = request.match_info["namespace"]
            record_id = request.match_info["record_id"]
            reason = request.query.get("reason", "sdk-purge")
            store = self._components.get("memory_store")
            auditor = self._components.get("purge_auditor")
            if store is None or auditor is None:
                return web.json_response(
                    {"error": "memory purge not configured"}, status=503
                )
            from agent_core.memory.purge import purge_memory as do_purge

            try:
                audit = await do_purge(store, auditor, ns, record_id, reason=reason)
            except KeyError as e:
                return web.json_response(
                    {"error": str(e.args[0])}, status=404
                )
            return web.json_response(
                PurgeResponse(
                    record_id=audit.record_id,
                    deleted_revisions=audit.deleted_revisions,
                    reason=audit.reason,
                ).model_dump()
            )

        return routes

    async def start(self) -> None:
        self._components = self._factory()
        app = web.Application(middlewares=[_bad_request_guard])
        app.add_routes(self._routes())
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

    async def stop(self) -> None:
        # 等后台 run 收口（有界：运行中的任务由 runtime 自身终态化）
        if self._bg:
            await asyncio.gather(*self._bg, return_exceptions=True)
        if self._runner:
            await self._runner.cleanup()

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"


def _build_exploration_policy(body):  # noqa: ANN001, ANN202 — 松耦合可选依赖
    """若环境内有 ExplorationPolicy（agent-explore），把 mode/max_waves 传入。"""
    try:
        from agent_explore.controller import ExplorationPolicy
    except ImportError:
        return None
    return ExplorationPolicy(mode=body.mode, max_waves=body.max_waves)

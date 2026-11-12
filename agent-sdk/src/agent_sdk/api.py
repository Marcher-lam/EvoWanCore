"""agent-sdk HTTP API — aiohttp 服务器暴露内核操作（V5 T1）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiohttp import web

from agent_sdk.models import (
    CancelRunResponse,
    EventResponse,
    ExploreResponse,
    RunStatusResponse,
    SubmitRunRequest,
)

AppFactory = Callable[[], dict[str, Any]]


class AgentSDKServer:
    """HTTP API 服务器——工厂函数返回含 runtime 等组件的字典。"""

    def __init__(self, app_factory: AppFactory, host: str = "127.0.0.1",
                 port: int = 8300) -> None:
        self._factory = app_factory
        self._host = host
        self._port = port
        self._components: dict[str, Any] = {}
        self._runner: web.AppRunner | None = None

    def _routes(self) -> web.RouteTableDef:
        routes = web.RouteTableDef()

        @routes.post("/runs")
        async def submit_run(request: web.Request) -> web.Response:
            body = SubmitRunRequest(**await request.json())
            from agent_core.task import TaskSpec

            task = TaskSpec(
                id=body.task_id, kind=body.kind, goal=body.goal,
                input_schema=body.input_schema, constraints=body.constraints,
                acceptance_criteria=body.acceptance_criteria,
            )
            agents = self._components.get("agents", {})
            agent = agents.get(body.agent_id) or next(iter(agents.values()))
            run = await self._components["runtime"].submit(task, agent)
            return web.json_response(
                RunStatusResponse(
                    run_id=run.id, state=run.state.value, task_id=run.task_id
                ).model_dump(), status=201
            )

        @routes.get("/runs/{run_id}")
        async def get_run(request: web.Request) -> web.Response:
            run_id = request.match_info["run_id"]
            run = await self._components["runtime"]._store.load_run(run_id)
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
            events = []
            async for e in self._components["runtime"].events(run_id):
                events.append(EventResponse(
                    id=e.id, type=e.type, ts=e.ts.isoformat(),
                    subject_type=e.subject_type, subject_id=e.subject_id,
                    payload=e.payload,
                ).model_dump())
            return web.json_response({"events": events, "count": len(events)})

        @routes.post("/runs/{run_id}/cancel")
        async def cancel_run(request: web.Request) -> web.Response:
            run_id = request.match_info["run_id"]
            await self._components["runtime"].cancel(run_id)
            return web.json_response(
                CancelRunResponse(run_id=run_id, cancelled=True).model_dump()
            )

        @routes.post("/explore")
        async def explore(request: web.Request) -> web.Response:
            await request.json()  # 解析验证（ExploreRequest 预留参数扩展）
            report = await self._components["app"].explore()
            return web.json_response(ExploreResponse(
                waves_executed=report.waves_executed,
                tasks_executed=report.tasks_executed,
                tasks_passed=report.tasks_passed,
                candidates_generated=report.candidates_generated,
                budget_spent=report.budget_spent,
            ).model_dump())

        return routes

    async def start(self) -> None:
        self._components = self._factory()
        app = web.Application()
        app.add_routes(self._routes())
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

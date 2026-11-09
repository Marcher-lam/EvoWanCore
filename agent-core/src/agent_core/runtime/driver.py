"""ExecutionPolicy / Verifier 契约与内置模型循环策略（spec §5.3）。"""

from __future__ import annotations

import json
import uuid
from typing import Literal, Protocol

from pydantic import BaseModel

from agent_core.action import ActionMode, ActionRequest, ActionResult
from agent_core.models import InferRequest, Modality, ModelGateway
from agent_core.run import RunHandle
from agent_core.task import TaskHandle


class AgentSpec(BaseModel):
    id: str
    role: str = "worker"
    capabilities: list[str] = []
    execution_policy: Literal["model_loop"] = "model_loop"
    model_id: str


class ExecutionPolicy(Protocol):
    async def propose_next_actions(self, run: RunHandle, context: dict) -> list[ActionRequest]: ...

    def should_yield(self, run: RunHandle, context: dict) -> bool: ...


class VerificationResult(BaseModel):
    passed: bool
    checks: list[dict] = []
    evidence_refs: list[str] = []


class Verifier(Protocol):
    async def verify_action(
        self, request: ActionRequest, result: ActionResult
    ) -> VerificationResult: ...

    async def verify_task(
        self, task: TaskHandle, evidence_refs: list[str]
    ) -> VerificationResult: ...


class ModelLoopPolicy:
    """内置执行策略：模型循环。LLM 提议动作，Runtime 决定执行（机制/策略分离）。"""

    def __init__(self, gateway: ModelGateway, agent: AgentSpec) -> None:
        self._gateway = gateway
        self._agent = agent

    async def propose_next_actions(self, run: RunHandle, context: dict) -> list[ActionRequest]:
        task = context["task"]
        messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    f"You are a {self._agent.role} agent. Task kind: {task.kind}. "
                    f"Goal: {json.dumps(task.goal, ensure_ascii=False)}. "
                    "Use tools to complete the task. Respond with tool calls, or text when done."
                ),
            }
        ]
        memory = context.get("memory")
        if memory:
            messages.append({"role": "system", "content": f"Relevant memory:\n{memory}"})
        for h in context.get("history", []):
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[action] {h['capability_id']}"
                        f"({json.dumps(h['arguments'], ensure_ascii=False)})"
                        f" -> {h['status']}"
                    ),
                }
            )
        resp = await self._gateway.route({"inputs": [Modality.TEXT]}).infer(
            InferRequest(
                model_id=self._agent.model_id,
                messages=messages,
                tools=context.get("capability_schemas") or None,
            )
        )
        return [
            ActionRequest(
                id=f"act_{uuid.uuid4().hex[:12]}",
                task_id=run.task_id,
                run_id=run.id,
                actor_id=self._agent.id,
                capability_id=tc["name"],
                arguments=tc.get("arguments", {}),
                mode=ActionMode.EXECUTE,
                justification={"source": "model_tool_call", "call_id": tc.get("id")},
            )
            for tc in resp.tool_calls
        ]

    def should_yield(self, run: RunHandle, context: dict) -> bool:  # noqa: ARG002
        return False  # 空提议由 Runtime 处理（转验证）

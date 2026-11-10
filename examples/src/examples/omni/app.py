"""OmniApp — 实时会话 Agent：已发布技能/记忆的消费者，不练习（spec §5.6）。

会话中 tool_call 经 CapabilityExecutor 执行（与批处理共用同一执行网关），
结果回传会话通道。实时 Agent 是经验的消费者而非练习者。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from agent_core.action import ActionMode, ActionRequest
from agent_core.models import SessionAdapter, SessionConfig
from agent_core.runtime.executor import CapabilityExecutor


@dataclass
class OmniSessionResult:
    session_id: str = ""
    transcripts: list[str] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    interrupted: int = 0


class OmniApp:
    def __init__(
        self,
        *,
        session_adapter: SessionAdapter,
        executor: CapabilityExecutor,
        instructions: str,
        model_id: str,
        voice: str | None = None,
    ) -> None:
        self._adapter = session_adapter
        self._executor = executor
        self._instructions = instructions
        self._model_id = model_id
        self._voice = voice

    async def _open(self) -> str:
        return await self._adapter.open_session(
            SessionConfig(
                model_id=self._model_id,
                voice=self._voice,
                instructions=self._instructions,
            )
        )


async def run_omni_session(omni: OmniApp, max_events: int = 50) -> OmniSessionResult:
    session_id = await omni._open()
    result = OmniSessionResult(session_id=session_id)
    events = 0
    async for ev in omni._adapter.stream(session_id):
        events += 1
        if events > max_events:
            break
        if ev.type == "transcript_out":
            result.transcripts.append(str(ev.payload.get("text", "")))
        elif ev.type == "interrupted":
            result.interrupted += 1
        elif ev.type == "tool_call":
            request = ActionRequest(
                id=f"act_{uuid.uuid4().hex[:10]}",
                task_id=f"omni:{session_id}",
                run_id=session_id,
                actor_id="omni_agent",
                capability_id=str(ev.payload.get("name", "")),
                arguments=dict(ev.payload.get("arguments", {})),
                mode=ActionMode.EXECUTE,
                justification={"source": "session_tool_call", "call_id": ev.payload.get("call_id")},
            )
            action_result = await omni._executor.dispatch(request)
            send_json = getattr(omni._adapter, "send_json", None)
            tool_result_message = {
                "type": "tool_result",
                "call_id": ev.payload.get("call_id"),
                "status": action_result.status.value,
                "output": action_result.output,
            }
            if send_json is not None:
                await send_json(session_id, tool_result_message)
            else:
                await omni._adapter.send_text(session_id, json.dumps(tool_result_message))
            result.tool_results.append(
                {
                    "call_id": ev.payload.get("call_id"),
                    "status": action_result.status.value,
                    "output": action_result.output,
                }
            )
        elif ev.type == "closed":
            break
    try:
        await omni._adapter.close_session(session_id)
    except Exception:  # noqa: BLE001 — 关闭失败不影响会话结果
        pass
    return result

"""ModelGateway 契约 — 能力协商、infer()/session() 双接口（spec §5.3）。

模型按能力声明被路由，不按类别标签；"音频输入 + 音频输出" 不能推断出
"支持双向实时会话"——interaction 是独立声明的维度。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    STRUCTURED = "structured"


Interaction = Literal["batch", "streaming", "realtime"]
ToolSupport = Literal["native_tool_call", "structured_action"]


class ModelCapabilities(BaseModel):
    model_id: str
    inputs: set[Modality] = {Modality.TEXT}
    outputs: set[Modality] = {Modality.TEXT}
    interaction: set[Interaction] = {"batch"}
    tools: set[ToolSupport] = set()
    constraints: dict = {}  # 上下文/分辨率/采样率/并发/地域/预算


class InferRequest(BaseModel):
    model_id: str
    messages: list[dict]
    tools: list[dict] | None = None
    structured_output: dict | None = None
    modalities: list[Modality] = [Modality.TEXT]


class InferResponse(BaseModel):
    text: str = ""
    tool_calls: list[dict] = []
    usage: dict = {}
    raw: dict | None = None


class SessionConfig(BaseModel):
    model_id: str
    voice: str | None = None
    instructions: str | None = None
    tools: list[dict] | None = None
    modalities: list[Modality] = [Modality.AUDIO]


SessionEventType = Literal[
    "opened",
    "transcript_in",
    "transcript_out",
    "tool_call",
    "audio_out",
    "interrupted",
    "session_resumed",
    "error",
    "closed",
]


class SessionEvent(BaseModel):
    type: SessionEventType
    payload: dict = {}


class ModelAdapter(Protocol):
    def capabilities(self) -> ModelCapabilities: ...

    async def infer(self, request: InferRequest) -> InferResponse: ...


class SessionAdapter(Protocol):
    def capabilities(self) -> ModelCapabilities: ...

    async def open_session(self, config: SessionConfig) -> str: ...

    def stream(self, session_id: str) -> AsyncIterator[SessionEvent]: ...

    async def send_text(self, session_id: str, text: str) -> None: ...

    async def send_audio(self, session_id: str, chunk: bytes) -> None: ...

    async def interrupt(self, session_id: str) -> None: ...

    async def close_session(self, session_id: str) -> None: ...


class NoModelAvailable(Exception):
    """没有任何已注册模型满足能力要求。"""


class ModelGateway:
    """注册-协商-路由。V1: infer 按需求路由，session 按 model_id 显式获取。"""

    def __init__(self) -> None:
        self._infer_adapters: list[ModelAdapter] = []
        self._session_adapters: dict[str, SessionAdapter] = {}

    def register(self, adapter: ModelAdapter) -> None:
        self._infer_adapters.append(adapter)

    def register_session(self, adapter: SessionAdapter) -> None:
        self._session_adapters[adapter.capabilities().model_id] = adapter

    def route(self, requirements: dict) -> ModelAdapter:
        need_inputs = {Modality(m) for m in requirements.get("inputs", [])}
        need_outputs = {Modality(m) for m in requirements.get("outputs", [])}
        need_interaction = requirements.get("interaction")
        need_tools = requirements.get("tools")
        for adapter in self._infer_adapters:
            caps = adapter.capabilities()
            if not need_inputs <= caps.inputs:
                continue
            if not need_outputs <= caps.outputs:
                continue
            if need_interaction and need_interaction not in caps.interaction:
                continue
            if need_tools and need_tools not in caps.tools:
                continue
            return adapter
        raise NoModelAvailable(f"没有模型满足: {requirements}")

    def session_adapter(self, model_id: str) -> SessionAdapter:
        return self._session_adapters[model_id]


class FakeSessionAdapter:
    """测试用会话适配器：内存事件队列，可脚本化推入事件。"""

    def __init__(self, caps: ModelCapabilities) -> None:
        self._caps = caps
        self._queues: dict[str, list[SessionEvent]] = {}
        self._sessions: dict[str, SessionConfig] = {}
        self.interrupts: list[str] = []
        self._counter = 0

    def capabilities(self) -> ModelCapabilities:
        return self._caps

    async def open_session(self, config: SessionConfig) -> str:
        self._counter += 1
        session_id = f"sess_{self._counter}"
        self._sessions[session_id] = config
        self._queues[session_id] = [SessionEvent(type="opened", payload={})]
        return session_id

    def push_event(self, session_id: str, event: SessionEvent) -> None:
        self._queues[session_id].append(event)

    async def stream(self, session_id: str):  # type: ignore[override]
        queue = self._queues[session_id]
        while queue:
            yield queue.pop(0)

    async def send_text(self, session_id: str, text: str) -> None:
        self._queues[session_id].append(SessionEvent(type="transcript_in", payload={"text": text}))

    async def send_audio(self, session_id: str, chunk: bytes) -> None:
        self._queues[session_id].append(
            SessionEvent(type="transcript_in", payload={"audio_len": len(chunk)})
        )

    async def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)
        self._queues[session_id].append(SessionEvent(type="interrupted", payload={}))

    async def close_session(self, session_id: str) -> None:
        self._queues[session_id].append(SessionEvent(type="closed", payload={}))


class FakeModelAdapter:
    """测试用脚本化适配器：按序弹出响应，记录请求；进程内零网络。"""

    def __init__(self, caps: ModelCapabilities, responses: list[InferResponse]) -> None:
        self._caps = caps
        self._responses = list(responses)
        self.recorded_requests: list[InferRequest] = []

    def capabilities(self) -> ModelCapabilities:
        return self._caps

    async def infer(self, request: InferRequest) -> InferResponse:
        self.recorded_requests.append(request)
        if not self._responses:
            raise AssertionError("脚本耗尽：FakeModelAdapter 没有更多响应")
        return self._responses.pop(0)


class CountingAdapter:
    """计量装饰器（V2 §6.2）：包装物理适配器，计数真实模型调用与 token。

    纯被动计数——不参与路由决策；预算口径 = calls（真实调用），
    total_tokens 按 InferResponse.usage["total_tokens"] 累计（缺失记 0）。
    """

    def __init__(self, inner: ModelAdapter) -> None:
        self._inner = inner
        self.calls: int = 0
        self.total_tokens: int = 0

    def capabilities(self) -> ModelCapabilities:
        return self._inner.capabilities()

    async def infer(self, request: InferRequest) -> InferResponse:
        resp = await self._inner.infer(request)
        self.calls += 1
        self.total_tokens += int(resp.usage.get("total_tokens", 0) or 0)
        return resp

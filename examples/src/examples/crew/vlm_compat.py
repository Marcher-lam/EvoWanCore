"""真模型兼容适配（V16-T4 实测缺陷修复）。

LM Studio qwen3.8-27b 的 jinja 模板要求 messages 含 user 角色——
crew 的纯文本任务只产生 system（goal 在 system 内），400
"No user query found"。本包装在 infer 前补一条 user 引导消息
（docworkbench 的 vision 任务天然带 user content part 故未触发）。
"""

from __future__ import annotations

from typing import Any

from agent_core.models import InferRequest, InferResponse


class UserRoleGuard:
    """包装真 VLM：system-only 消息 → 插入 user 引导（供应商模板怪癖）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def capabilities(self):
        return self._inner.capabilities()

    async def infer(self, request: InferRequest) -> InferResponse:
        self.calls += 1
        has_user = any(m.get("role") == "user" for m in request.messages)
        if not has_user:
            messages = list(request.messages)
            # 插在 system 之后：作为用户请求引导模型按系统指令行动
            insert_at = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(insert_at, {
                "role": "user",
                "content": "请按系统指令处理当前票据任务，用工具调用完成。",
            })
            request = request.model_copy(update={"messages": messages})
        return await self._inner.infer(request)

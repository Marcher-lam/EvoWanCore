"""CrewFakeModel——剧本化模型（V14）：按工具面/历史感知"技能存在与否"。

感知面（ModelLoopPolicy 构造）：messages 含 system Goal + history 回显
`[action] cap(args) -> status`；tools = 能力目录 schema。
剧本：干净直交 / 脏无技能三拒放弃 / 脏有技能先调技能再交归一化值。
"""

from __future__ import annotations

from agent_core.models import (
    InferRequest,
    InferResponse,
    Modality,
    ModelCapabilities,
)

# 脏模式 → 归一化技能 id（evolve 注册后出现在能力目录）
SKILL_FOR_MODE = {
    "date_format": "normalize_date",
    "currency_symbol": "normalize_amount",
    "tax_rate": "correct_tax_rate",
}


def _dirty_modes(payload: dict) -> list[str]:
    modes = []
    if "/" in str(payload.get("date", "")):
        modes.append("date_format")
    if isinstance(payload.get("amount"), str) or payload.get("amount_raw"):
        modes.append("currency_symbol")
    if payload.get("tax_rate") == 0.03:
        modes.append("tax_rate")
    return modes


class CrewFakeModel:
    def __init__(self, env_holder: dict) -> None:
        self._env_holder = env_holder
        self._caps = ModelCapabilities(
            model_id="crew-fake",
            inputs={Modality.TEXT}, tools={"native_tool_call"},
        )

    def _skill_output(self, cap: str) -> dict:
        """技能真实沙箱输出（终评审 H2：不本地模拟，消费真 stdout）。"""
        env = self._env_holder.get("env")
        return (env.skill_outputs.get(cap) if env else None) or {}

    def capabilities(self) -> ModelCapabilities:
        return self._caps

    async def infer(self, request: InferRequest) -> InferResponse:
        payload = _extract_payload(request.messages)
        dirty = _dirty_modes(payload)
        tool_ids = {
            t["function"]["name"] for t in (request.tools or [])
        }
        history = [
            m for m in request.messages
            if m.get("role") == "user"
            and str(m.get("content", "")).startswith("[action]")
        ]
        n = len(history)

        def _call(cap: str, args: dict) -> InferResponse:
            return InferResponse(tool_calls=[{
                "id": f"c{n}", "name": cap, "arguments": args,
            }])

        def _submit(fields: dict) -> InferResponse:
            return _call("submit_extracted_fields", {"fields": fields})

        clean_fields = {
            "amount": payload.get("amount"),
            "date": payload.get("date"),
            "tax_rate": payload.get("tax_rate"),
        }

        if n == 0:
            if not dirty:
                return _submit(clean_fields)  # 干净：1 步直通
            skillable = [
                m for m in dirty if SKILL_FOR_MODE[m] in tool_ids
            ]
            if skillable:  # 有技能：先调第一个可用技能
                cap = SKILL_FOR_MODE[skillable[0]]
                args = {"value": _dirty_value(payload, skillable[0])}
                return _call(cap, args)
            return _submit(clean_fields)  # 无技能：原样交（被拒）
        # n>0：看最近动作
        last = str(history[-1]["content"])
        if "-> succeeded" in last:
            if any(
                last.startswith(f"[action] {SKILL_FOR_MODE[m]}(")
                for m in dirty
            ):
                # 技能刚执行完：字段取技能真实沙箱输出（缺失则原样——
                # 技能无效时提交脏值被拒，指标如实反映）
                fields = {
                    "amount": payload.get("amount"),
                    "date": payload.get("date"),
                    "tax_rate": payload.get("tax_rate"),
                }
                for m in dirty:
                    out = self._skill_output(SKILL_FOR_MODE[m])
                    if "normalized" not in out:
                        continue  # 技能没产出（无效技能）→ 不修
                    key = {
                        "date_format": "date",
                        "currency_symbol": "amount",
                        "tax_rate": "tax_rate",
                    }[m]
                    fields[key] = out["normalized"]
                return _submit(fields)
            # submit 已成功（本不应到——succeeded 后 policy 结束）
            return InferResponse(text="done")
        # 最近失败（submit 被拒）
        if n >= 3:
            return InferResponse(text="give up")  # 三拒放弃
        skillable = [m for m in dirty if SKILL_FOR_MODE[m] in tool_ids]
        if skillable and n == 1:
            # 第二次尝试：仍有技能没用（多脏模式场景）→ 调下一个技能
            cap = SKILL_FOR_MODE[skillable[0]]
            return _call(cap, {"value": _dirty_value(payload, skillable[0])})
        # 瞎改一版再交（仍错——脏值无法凭空修复）
        wrong = dict(clean_fields)
        if "date_format" in dirty:
            wrong["date"] = str(clean_fields["date"]) + "x"
        elif "tax_rate" in dirty:
            wrong["tax_rate"] = 0.99
        else:
            wrong["amount"] = -1
        return _submit(wrong)


def _dirty_value(payload: dict, mode: str) -> str:
    if mode == "date_format":
        return str(payload.get("date", ""))
    if mode == "currency_symbol":
        return str(payload.get("amount_raw", ""))
    return str(payload.get("tax_rate", ""))


def _extract_payload(messages: list[dict]) -> dict:
    from examples.crew.solver import parse_goal_payload

    return parse_goal_payload(messages)

"""crew solver（V14-T2）——票据抽取单任务：AgentRuntime 真循环 + Fake 剧本。

剧本语义（可量化"变强"的来源）：
- 干净票据：1 步直通（submit 正确值）
- 脏票据 + 无技能：submit 原样被拒 → 瞎改再拒 → 三拒 → 放弃（≥3 步失败）
- 脏票据 + 有技能：调归一化技能 → submit 归一化值（2 步直通）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from examples.crew.domain import Invoice, grader


@dataclass
class SolveResult:
    passed: bool
    error_tags: list[str]
    steps: int  # 口径：该 run 的 ActionDispatched 事件数（公有事件流）
    rejected_submits: int = 0  # 被拒提交数（弯路指标——学习增益的度量）
    stages: list[str] = field(default_factory=list)  # 流水线阶段记录
    needs_review: bool = False  # audit 打回（大额复核）


class CrewEnv:
    """共享环境：能力目录 + runtime + 当前票据的评分状态。

    单飞不变量：状态按"当前票据"存（reset_for 清零）——同一 env 只支持
    串行 submit（并发同 env 会串数据；多任务并发需按 task 键控，待需求）。
    """

    def __init__(self, registry: Any, runtime: Any) -> None:
        self.registry = registry
        self.runtime = runtime
        self.current_truth: Invoice | None = None
        self.last_pass = False
        self.last_errors: list[str] = []
        # 技能真实输出（沙箱 stdout）——Fake 提交值取自这里（终评审 H2：
        # 指标必须对"技能是否真有效"敏感，本地第二实现会自导自演）
        self.skill_outputs: dict[str, dict] = {}
        # 当前票据动作数（口径：ActionDispatched；编排路径 run_id 与事件
        # correlation 不可直连，单飞不变量下 env 计数最可靠）
        self.dispatch_count = 0
        # 三阶段流水线状态（classify 预判 / audit 复核）
        self.last_classification: dict = {}
        self.last_audit: dict = {}
        # 当前阶段完成标志（各 submit handler 成功置位；verifier 统一判据）
        self.stage_pass = False

    def reset_for(self, inv: Invoice) -> None:
        self.current_truth = inv
        self.last_pass = False
        self.last_errors = []
        self.skill_outputs = {}
        self.dispatch_count = 0
        self.rejected_submits = 0
        self.last_classification = {}
        self.last_audit = {}
        self.stage_pass = False


def make_solver_env(
    tmp_path: Path, *, max_steps: int = 8, memory=None, vlm=None,
) -> CrewEnv:
    """组装：submit 能力 + 模型 + AgentRuntime（真内核循环）。

    memory: CrewMemory——接通 B 闭环（规则记忆祈使渲染进模型上下文）。
    vlm: 模型适配器（默认 CrewFakeModel；realrun 传 LMStudioVLM——
    Fake 与真模型同契约：capabilities() + infer(InferRequest)）。
    """
    from agent_core.capability import (
        CapabilityDescriptor,
        CapabilityType,
        InMemoryCapabilityRegistry,
    )
    from agent_core.evidence import SqliteEvidenceStore
    from agent_core.models import ModelGateway
    from agent_core.policy import DefaultPolicyEngine
    from agent_core.runtime.driver import VerificationResult
    from agent_core.runtime.eventlog import SqliteEventLog
    from agent_core.runtime.runtime import AgentRuntime
    from agent_core.runtime.statestore import SqliteStateStore

    registry = InMemoryCapabilityRegistry()
    env_holder: dict[str, CrewEnv] = {}

    def _as_dict(args):
        """27b 怪癖（V3/V4 同款）：tool arguments 可能是 JSON 字符串。"""
        import json as _json

        value = args
        for _ in range(2):  # 双层（整体 str + 字段 str）
            if isinstance(value, str):
                try:
                    value = _json.loads(value)
                except ValueError:
                    env = env_holder["env"]
                    if env is not None:
                        env.last_errors = ["malformed_json"]
                    return {"__malformed__": True}
        return value

    async def _submit(args: dict) -> dict:
        env = env_holder["env"]
        assert env and env.current_truth is not None
        args = _as_dict(args)
        fields = _as_dict(args.get("fields", args))
        if fields.get("__malformed__"):
            env.last_errors = ["malformed_json"]
            raise ValueError("REJECTED:malformed_json")
        ok, errors = grader(fields, env.current_truth)
        if not ok:
            env.last_errors = list(errors)  # 反射器据此产技能候选
            env.rejected_submits += 1  # 弯路指标
            raise ValueError(f"REJECTED:{','.join(errors)}")
        env.last_pass = True
        env.stage_pass = True
        return {"ok": True, "fields": fields}

    registry.bind(
        CapabilityDescriptor(
            id="submit_extracted_fields", type=CapabilityType.TOOL,
            name="submit", input_schema={"fields": "object"},
            side_effect_level="read", auth_scope=[], tags=["crew"],
        ), _submit)

    async def _classify(args: dict) -> dict:
        env = env_holder["env"]
        args = _as_dict(args)
        env.last_classification = dict(args.get("result", args))
        env.stage_pass = True
        return {"ok": True}

    registry.bind(
        CapabilityDescriptor(
            id="submit_classification", type=CapabilityType.TOOL,
            name="classify", input_schema={"result": "object"},
            side_effect_level="read", auth_scope=[], tags=["crew"],
        ), _classify)

    async def _audit(args: dict) -> dict:
        env = env_holder["env"]
        args = _as_dict(args)
        env.last_audit = dict(args.get("result", args))
        env.stage_pass = True
        return {"ok": True}

    registry.bind(
        CapabilityDescriptor(
            id="submit_audit", type=CapabilityType.TOOL,
            name="audit", input_schema={"result": "object"},
            side_effect_level="read", auth_scope=[], tags=["crew"],
        ), _audit)

    from examples.crew.fake_model import CrewFakeModel

    model = vlm if vlm is not None else CrewFakeModel(env_holder)
    gateway = ModelGateway()
    gateway.register(model)

    class _CrewVerifier:
        async def verify_task(self, task, evidence_refs):
            # 统一判据：当前阶段的 submit handler 成功置位 stage_pass
            # （TaskHandle 不带 kind——阶段语义由 handler 表达）
            env = env_holder["env"]
            return VerificationResult(
                passed=bool(env and env.stage_pass),
                evidence_refs=list(evidence_refs),
            )

    runtime = AgentRuntime(
        gateway=gateway, registry=registry,
        policy_engine=DefaultPolicyEngine(),
        event_log=SqliteEventLog(tmp_path / "crew_log.db"),
        state_store=SqliteStateStore(tmp_path / "crew_state.db"),
        evidence_store=SqliteEvidenceStore(
            tmp_path / "crew_ev.db", tmp_path / "crew_blobs"),
        verifier=_CrewVerifier(), max_steps=max_steps,
        # B 闭环接线：记忆检索 + 祈使渲染（memory=None 时零影响）
        **(
            {"memory_retriever": memory.retriever(),
             "context_assembler": memory.assembler(),
             "memory_token_budget": 400}
            if memory is not None else {}
        ),
    )
    env = CrewEnv(registry, runtime)
    env_holder["env"] = env

    async def _hook(request, result):
        env.dispatch_count += 1
        # 技能动作：捕获沙箱真实 stdout（JSON）供 Fake 提交消费
        if request.capability_id in {
            "normalize_date", "normalize_amount", "correct_tax_rate",
            "normalize_amount_words",
        } and result.status.value == "succeeded":
            out = result.output or {}
            if isinstance(out.get("normalized"), (str, int, float)):
                env.skill_outputs[request.capability_id] = out

    # 一次性包装（每票重复包装会重复触发——实测踩过）
    runtime._executor = _HookExecutor(runtime._executor, _hook)  # noqa: SLF001
    return env


async def solve_invoice(env: CrewEnv, inv: Invoice, payload: dict) -> SolveResult:
    """单票据：submit 任务 → 真循环跑完 → 评分。"""
    from agent_core.runtime.driver import AgentSpec
    from agent_core.task import TaskSpec

    env.reset_for(inv)
    task = TaskSpec(
        id=f"crew_{inv.invoice_id}", kind="invoice_extract",
        goal={"payload": payload}, input_schema={}, constraints={},
        acceptance_criteria=[{"check": "fields_match"}],
    )
    agent = AgentSpec(id="extractor", role="invoice extractor",
                      model_id="crew-fake")

    run = await env.runtime.submit(task, agent)
    passed = run.state.value == "succeeded" and env.last_pass
    steps = len([
        e async for e in env.runtime.events(run.id)
        if e.type == "ActionDispatched"
    ])
    return SolveResult(
        passed=passed, error_tags=list(env.last_errors), steps=steps,
        rejected_submits=env.rejected_submits,
    )


class _HookExecutor:
    """包装 executor 记录动作轨迹（步数统计）。"""

    def __init__(self, inner: Any, hook: Any) -> None:
        self._inner = inner
        self._hook = hook

    async def dispatch(self, request):
        result = await self._inner.dispatch(request)
        await self._hook(request, result)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def parse_goal_payload(messages: list[dict]) -> dict:
    """从 system prompt 的 Goal json 提取 payload（Fake 模型感知面）。"""
    for m in messages:
        if m.get("role") == "system":
            text = m.get("content", "")
            idx = text.find('"payload"')
            if idx != -1:
                start = text.find("{", idx - 1)
                depth = 0
                for i in range(start, len(text)):
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                parsed = json.loads(text[start:i + 1])
                                return parsed.get("payload", parsed)
                            except json.JSONDecodeError:
                                break
    return {}

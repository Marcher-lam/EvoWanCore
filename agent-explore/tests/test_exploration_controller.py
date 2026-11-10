"""T20 BRS/DRS 探索控制器：并行波次、崩溃隔离、冲突转化、先记忆后出题。"""

import json
from pathlib import Path

from agent_core.capability import InMemoryCapabilityRegistry
from agent_core.evidence import SqliteEvidenceStore
from agent_core.memory.record import VerificationState
from agent_core.memory.store import NS_RULES, SqliteMemoryStore
from agent_core.memory.writer import DefaultConsolidator, DefaultMemoryWriter
from agent_core.models import (
    FakeModelAdapter,
    InferResponse,
    Modality,
    ModelCapabilities,
    ModelGateway,
)
from agent_core.policy import DefaultPolicyEngine
from agent_core.runtime.driver import AgentSpec, VerificationResult
from agent_core.runtime.eventlog import SqliteEventLog
from agent_core.runtime.runtime import AgentRuntime
from agent_core.runtime.statestore import SqliteStateStore
from agent_core.task import TaskSpec
from agent_explore.controller import ExplorationController, ExplorationPolicy
from agent_explore.curriculum import LLMCurriculumGenerator
from agent_explore.curriculum_memory import DefaultAssembler
from agent_explore.gaps import SqliteGapLedger

# ---------- 测试环境 ----------


class StubVerifier:
    def __init__(self, fail_for: set[str] | None = None, explode_for: set[str] | None = None):
        self.fail_for = fail_for or set()
        self.explode_for = explode_for or set()

    async def verify_action(self, request, result):
        return VerificationResult(passed=True)

    async def verify_task(self, task, evidence_refs):
        if task and task.spec_id in self.explode_for:
            raise RuntimeError(f"verifier crashed for {task.spec_id}")
        passed = task is not None and task.spec_id not in self.fail_for
        return VerificationResult(passed=passed, checks=[{"stub": passed}])


def _task_json(family: str, title: str) -> dict:
    return {
        "id": title,  # 固定 id，便于 verifier 定向引爆/判分
        "family": family,
        "title": title,
        "objective": {"what": title},
        "acceptance": {
            "verifier_profile": "p",
            "required_evidence": [],
            "pass_conditions": [{"check": "done"}],
            "fail_conditions": [],
        },
        "tags": [title],
    }


def _wave_plan_json(snapshot_should_ignore: str, tasks: list[dict]) -> str:
    return json.dumps(
        {
            "wave": {
                "shared_memory_snapshot_id": "llm_said_this",
                "task_ids": [],
                "parallelism": 2,
            },
            "tasks": tasks,
        }
    )


class Env:
    def __init__(
        self, tmp_path: Path, responses: list[InferResponse], verifier=None, reflector=None
    ):
        self.registry = InMemoryCapabilityRegistry()
        self.gateway = ModelGateway()
        self.fake = FakeModelAdapter(
            ModelCapabilities(model_id="m-x", inputs={Modality.TEXT}), list(responses)
        )
        self.gateway.register(self.fake)
        self.event_log = SqliteEventLog(tmp_path / "log.db")
        self.state_store = SqliteStateStore(tmp_path / "state.db")
        self.memory_store = SqliteMemoryStore(tmp_path / "mem.db")
        self.evidence = SqliteEvidenceStore(tmp_path / "ev.db", tmp_path / "blobs")
        self.gap_ledger = SqliteGapLedger(tmp_path / "gaps.db")
        self.verifier = verifier or StubVerifier()
        self.runtime = AgentRuntime(
            gateway=self.gateway,
            registry=self.registry,
            policy_engine=DefaultPolicyEngine(),
            event_log=self.event_log,
            state_store=self.state_store,
            evidence_store=self.evidence,
            verifier=self.verifier,
        )
        self.agent = AgentSpec(id="agent_x", role="worker", model_id="m-x")
        self.log: list[str] = []
        self.reflector = reflector or (lambda task, outcome: [])
        self.extra_context: dict = {}
        self.controller = ExplorationController(
            curriculum=LLMCurriculumGenerator(gateway=self.gateway, model_id="m-x"),
            assembler=DefaultAssembler(),
            writer=DefaultMemoryWriter(self.memory_store),
            consolidator=DefaultConsolidator(self.memory_store),
            memory_store=self.memory_store,
            gap_ledger=self.gap_ledger,
        )

    def context(self) -> dict:
        return {
            "runtime": self.runtime,
            "agent": self.agent,
            "reflector": self.reflector,
            "environment_version": "1.0",
            **self.extra_context,
        }

    async def run(self, mode: str = "broad", *, max_waves: int = 2, max_deep: int = 3):
        target = TaskSpec(
            id="target_1", kind="extract", goal={}, input_schema={},
            constraints={}, acceptance_criteria=[{"c": 1}],
        )
        return await self.controller.explore(
            target,
            ExplorationPolicy(mode=mode, max_waves=max_waves, max_deep=max_deep),
            verifier=self.verifier,
            app_context=self.context(),
        )


# ---------- BRS ----------


async def test_brs_wave_runs_parallel_and_merges_sequentially(tmp_path: Path) -> None:
    plan = _wave_plan_json(
        "x",
        [_task_json("prerequisite", "识别按钮"), _task_json("variant", "换文件类型导出")],
    )
    env = Env(
        tmp_path,
        responses=[InferResponse(text=plan), InferResponse(text="ok"), InferResponse(text="ok")],
        reflector=lambda task, outcome: [
            {
                "kind": "rule",
                "content": {
                    "statement": f"经验:{task.title}",
                    "claims": [],
                    "evidence_checked": [],
                },
                "applicability": {"app": "workbench", "topic": task.title},
                "evidence_refs": ["evi_x"],
            }
        ]
        if outcome.status == "passed"
        else [],
    )
    report = await env.run(mode="broad", max_waves=1)

    assert report.waves_executed == 1
    assert report.tasks_executed == 2
    assert report.tasks_passed == 2
    assert report.candidates_generated == 2
    # 波次任务从同一记忆快照出发
    plan_req = env.fake.recorded_requests[0]
    assert "snap_0" in plan_req.messages[-1]["content"] or "snap" in json.dumps(
        [m["content"] for m in plan_req.messages]
    )
    # 合并按序落库：两条经验已提交（CANDIDATE）
    rules = await env.memory_store.query(NS_RULES, {})
    assert len(rules) == 2
    assert all(r.verification is VerificationState.CANDIDATE for r in rules)


async def test_single_task_crash_does_not_kill_wave(tmp_path: Path) -> None:
    plan = _wave_plan_json(
        "x",
        [_task_json("prerequisite", "会炸的任务"), _task_json("variant", "正常任务")],
    )
    env = Env(
        tmp_path,
        responses=[InferResponse(text=plan), InferResponse(text="ok"), InferResponse(text="ok")],
        verifier=StubVerifier(explode_for={"会炸的任务"}),
        reflector=lambda task, outcome: [],
    )
    report = await env.run(mode="broad", max_waves=1)

    # 一个任务崩了（verifier 抛异常），姊妹任务照常完成
    assert report.tasks_executed == 2
    assert report.tasks_passed == 1
    # 崩溃以 outcome 记录，不传播
    assert report.waves_executed == 1  # 波没有被取消


async def test_conflict_becomes_contrast_task_next_wave(tmp_path: Path) -> None:
    wave1 = _wave_plan_json("x", [_task_json("variant", "甲"), _task_json("variant", "乙")])
    wave2 = _wave_plan_json("x", [_task_json("contrast", "对比甲乙")])
    conclusions = {"甲": True, "乙": False}

    def reflector(task, outcome):
        return [
            {
                "kind": "rule",
                "content": {
                    "subject": "同一主题",
                    "conclusion": conclusions.get(task.title, True),
                    "claims": [],
                    "evidence_checked": [],
                },
                "applicability": {"app": "workbench"},  # 完全相同条件
                "evidence_refs": ["evi"],
            }
        ]

    env = Env(
        tmp_path,
        responses=[
            InferResponse(text=wave1),
            InferResponse(text="ok"),
            InferResponse(text="ok"),
            InferResponse(text=wave2),
            InferResponse(text="ok"),
        ],
        reflector=reflector,
    )
    await env.run(mode="broad")

    # 同条件反结论 → DISPUTED（不覆盖）。I4 修复后：第二波 contrast 任务的结论
    # （True）与已存规则乙（False）同主题反结论，也必须被抓 → 3 条全部 DISPUTED
    rules = await env.memory_store.query(NS_RULES, {})
    disputed = [r for r in rules if r.verification is VerificationState.DISPUTED]
    assert len(disputed) == 3
    assert len(rules) == 3
    # 冲突提示进入下一波课程输入
    assert any(
        "contrast" in json.dumps([m["content"] for m in r.messages]).lower()
        for r in env.fake.recorded_requests
    ), "第二波课程请求应包含冲突提示"


# ---------- DRS ----------


async def test_drs_integrates_memory_before_next_task(tmp_path: Path) -> None:
    task_jsons = [
        json.dumps(_task_json("target_attempt", "直接导出")),
        json.dumps(_task_json("prerequisite", "补前置")),
    ]
    env = Env(
        tmp_path,
        responses=[
            InferResponse(text=task_jsons[0]),
            InferResponse(text="ok"),
            InferResponse(text=task_jsons[1]),
            InferResponse(text="ok"),
        ],
        reflector=lambda task, outcome: [
            {
                "kind": "rule",
                "content": {"statement": "整合的记忆", "claims": [], "evidence_checked": []},
                "applicability": {"app": "workbench"},
                "evidence_refs": ["evi"],
            }
        ]
        if outcome.status == "passed"
        else [],
    )

    # 包装 curriculum 记录调用顺序
    order: list[str] = env.log
    orig_refine = env.controller._curriculum.refine_after_result

    async def spy_refine(prev_task, outcome, diagnosis, view):
        rules = await env.memory_store.query(NS_RULES, {})
        order.append(f"refine(mem={len(rules)})")
        return await orig_refine(prev_task, outcome, diagnosis, view)

    env.controller._curriculum.refine_after_result = spy_refine  # type: ignore[method-assign]

    report = await env.run(mode="deep", max_deep=2)
    # 第二次出题时，第一次的记忆已经整合（refine(mem=1) 在 refine(mem=0) 之后）
    assert order[0] == "refine(mem=0)"
    assert order[1] == "refine(mem=1)"
    assert report.candidates_generated == 2  # 两步练习都通过，各整合一条


async def test_report_accounts_budget(tmp_path: Path) -> None:
    plan = _wave_plan_json("x", [_task_json("prerequisite", "t1")])
    env = Env(
        tmp_path,
        responses=[InferResponse(text=plan), InferResponse(text="ok")],
    )
    report = await env.run(mode="broad", max_waves=1)
    assert report.budget_spent.get("actions", 0) >= 1
    assert report.gaps_opened == 0


async def test_new_candidate_conflicts_with_published_rule(tmp_path: Path) -> None:
    """I4 回归：新候选与已发布规则同条件反结论 → 双双 DISPUTED（不共存）。"""
    # 预先存在一条已发布规则：主题 T，结论 True
    from agent_core.memory.record import (
        MemoryKind,
        MemoryRecord,
        VerificationState,
    )

    published = MemoryRecord(
        kind=MemoryKind.RULE,
        namespace=NS_RULES,
        content={"subject": "主题T", "conclusion": True, "claims": [], "evidence_checked": []},
        applicability={"app": "workbench"},
        evidence_refs=["evi_pre"],
        verification=VerificationState.VERIFIED,
    )

    plan = _wave_plan_json("x", [_task_json("variant", "新观察")])
    env = Env(
        tmp_path,
        responses=[InferResponse(text=plan), InferResponse(text="ok")],
        # reflector 产出同条件、同主题、反结论的候选
        reflector=lambda task, outcome: [
            {
                "kind": "rule",
                "content": {"subject": "主题T", "conclusion": False,
                            "claims": [], "evidence_checked": []},
                "applicability": {"app": "workbench"},
                "evidence_refs": ["evi_new"],
            }
        ],
    )
    await env.memory_store.put(published)
    await env.run(mode="broad", max_waves=1)

    rules = await env.memory_store.query(NS_RULES, {})
    by_subject_t = [r for r in rules if r.content.get("subject") == "主题T"]
    assert len(by_subject_t) == 2
    assert all(r.verification is VerificationState.DISPUTED for r in by_subject_t), (
        "新旧反结论不得共存，必须双双 DISPUTED"
    )


async def test_failed_practice_opens_gap(tmp_path: Path) -> None:
    """I5 回归：失败的练习必须记入 GapLedger（gap-driven 闭环的输入）。"""
    from agent_explore.gaps import GapType

    plan = _wave_plan_json("x", [_task_json("prerequisite", "会失败的任务")])
    env = Env(
        tmp_path,
        responses=[InferResponse(text=plan), InferResponse(text="ok")],
        verifier=StubVerifier(fail_for={"会失败的任务"}),
        reflector=lambda task, outcome: [],
    )
    report = await env.run(mode="broad", max_waves=1)
    assert report.tasks_passed == 0
    assert report.gaps_opened == 1
    gaps = await env.gap_ledger.open_gaps({})
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.KNOWLEDGE
    assert "会失败的任务" in gaps[0].title


async def test_practice_goal_carries_doc_context_from_pool(tmp_path: Path) -> None:
    """练习任务的 goal 必须携带池内任务的 doc_id（模型据此选文档）。"""
    plan = _wave_plan_json("x", [_task_json("variant", "抽取练习")])
    env = Env(
        tmp_path,
        responses=[InferResponse(text=plan), InferResponse(text="ok")],
        reflector=lambda task, outcome: [],
    )
    env.extra_context["practice_pool"] = [
        {"id": "抽取练习", "dimension": "extract", "doc_id": "doc_9", "goal": "抽取字段"}
    ]
    await env.run(mode="broad", max_waves=1)
    # 练习提交的模型请求应包含 doc_id
    all_content = " ".join(
        m["content"] for r in env.fake.recorded_requests for m in r.messages
    )
    assert "doc_9" in all_content, "练习 goal 必须带 doc_id"


async def test_wave_parallelism_enforced(tmp_path: Path) -> None:
    """M9a: parallelism 生效——并发峰值 ≤ 限值，波内任务全部完成。"""
    import asyncio

    tasks = [_task_json("variant", f"任务{i}") for i in range(4)]
    plan = _wave_plan_json("x", tasks)  # parallelism=2（默认构造为 4，见下）
    peak = {"now": 0, "max": 0}

    class PeakVerifier(StubVerifier):
        async def verify_task(self, task, evidence_refs):
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.02)
            peak["now"] -= 1
            return await super().verify_task(task, evidence_refs)

    env = Env(
        tmp_path,
        responses=[InferResponse(text=plan)] + [InferResponse(text="done")] * 4,
        verifier=PeakVerifier(),
    )
    report = await env.run(mode="broad", max_waves=1)
    assert report.tasks_executed == 4
    assert peak["max"] <= 2, f"并发峰值 {peak['max']} 超过 parallelism=2"

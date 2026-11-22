"""crew wave 循环（V14-T4）——探索（失败即 gap）+ 自进化（技能沉淀）。

每 wave：solve 全批 → grader → 失败模式驱动技能进化（去重：已注册跳过）
→ 下 wave 有技能直通。指标：成功率 / 平均步数 / 技能数 / 失败分布。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from examples.crew.domain import Invoice
from examples.crew.evolve import register_skill_for_mode
from examples.crew.explore import expand_with_gaps
from examples.crew.solver import CrewEnv, SolveResult


@dataclass
class WaveMetrics:
    wave: int
    total: int
    passed: int
    success_rate: float
    avg_steps: float
    skills: int
    failures_by_mode: dict[str, int] = field(default_factory=dict)
    recovered: int = 0  # 当 wave 自愈数（进化后重派救回的失败票）
    total_rejected: int = 0  # 被拒提交总数（学习增益：弯路指标）
    needs_review_count: int = 0  # audit 打回数（合规复核——有消费的旗标）
    # V18 探针指标（与 eval 批口径分离——探针不污染批指标）
    probe_total: int = 0
    probe_failures_by_mode: dict[str, int] = field(default_factory=dict)
    probe_recovered: int = 0  # 进化后探针重派救回数（当 wave 闭环验证）


def crew_agents() -> list:
    """三角色（classify/extract/audit 各自能力标签——编排按 kind 选人）。"""
    from agent_core.runtime.driver import AgentSpec

    return [
        AgentSpec(id="classifier", role="classifier",
                  capabilities=["classify"], model_id="crew-fake"),
        AgentSpec(id="extractor", role="extractor",
                  capabilities=["invoice_extract"], model_id="crew-fake"),
        AgentSpec(id="auditor", role="auditor",
                  capabilities=["audit"], model_id="crew-fake"),
    ]


class InvoiceOpsCrew:
    """多 Agent 编排：票据任务经 Orchestrator 按 kind 选 extractor 执行；
    wave 末进化出新技能后立即对失败票重派（当 wave 自愈）。
    Ruling：三角色共用单 runtime/单 env——串行安全；并行多任务需按
    task 键控 env 状态 + 每 agent 独立 runtime（待分布式需求）。
    """

    def __init__(self, env: CrewEnv, *, workspace: Path,
                 memory=None, tool_first: bool = False) -> None:
        from agent_core.orchestration import (
            CapabilityCompositionPolicy,
            Orchestrator,
        )

        self._env = env
        self._workspace = workspace
        self._memory = memory  # B 闭环记忆（学习全链的存储侧）
        # V17 工具优先：goal 注入指令——真 27b 实测倾向自己心算归一化
        # 而绕过已进化技能（V16 realrun wave2/3 成功率回落根因）
        self._tool_first = tool_first
        self._evolved: set[str] = set()
        self._probe_ids: set[str] = set()  # 探针票 id（跨 wave 去重）
        self._generation = 0
        self._agents = crew_agents()
        # V5 Orchestrator 原生同步策略（StaticPolicyAdapter 是给 async 版
        # ReplanningOrchestrator 的——用错会 coroutine not iterable）
        self._orchestrator = Orchestrator(
            CapabilityCompositionPolicy(),
            runtimes={a.id: env.runtime for a in self._agents},
        )

    async def run_waves(
        self, batch: list[tuple[Invoice, dict]], *, n_waves: int = 3,
        pipeline: bool = False, curriculum: bool = False,
        probe: bool = False,
    ) -> list[WaveMetrics]:
        """wave 循环（四能力全景入口）。

        pipeline=True：solve 走三阶段流水线（classify→extract→audit）；
        curriculum=True：批序列由 curriculum_batches 生成（难度递进），
        传入的 batch 作为 wave1 起点 + gap_driven 扩充下轮；
        probe=True：反饥饿探索（V18）——每 wave 批解算后主动合成难例
        （frontier 未进化领地 + 已进化技能边界）试解，失败并流
        反射器/进化器：批全过时学习不再断粮（"变强饿死学习"对策）。
        """
        metrics: list[WaveMetrics] = []
        batches = self._plan_batches(batch, n_waves, curriculum)
        seen_ids = {inv.invoice_id for inv, _ in batches[0]}
        for w in range(1, n_waves + 1):
            current = batches[w - 1]
            results = await self._solve_batch(current, pipeline=pipeline)
            m = self._metrics(w, results)
            # 指标先记（skills/成功率 = 初解面貌），进化后行
            if w < n_waves:  # 末 wave 不再进化（指标即战果）
                probe_results = await self._run_probes(m) if probe else []
                learn_from = list(results) + probe_results
                await self._evolve_from(learn_from)
                await self._learn_from(learn_from)  # B 闭环：规则记忆沉淀
                m.recovered = await self._retry_failures(current, results)
                if probe_results:
                    m.probe_recovered = await self._retry_failures(
                        self._last_probe_pairs, probe_results,
                    )
                # gap_driven：失败模式新票注入下轮（curriculum 模式）
                if curriculum:
                    batches[w] = expand_with_gaps(
                        batches[w], m.failures_by_mode,
                        existing_ids=seen_ids,
                    )
                    seen_ids.update(
                        inv.invoice_id for inv, _ in batches[w])
            metrics.append(m)
        return metrics

    async def _run_probes(self, m: WaveMetrics) -> list[SolveResult]:
        """反饥饿探索：合成难例 → 试解 → 失败归因（合成真值标签）。

        探针失败覆盖 grader 的字段级盲猜标签（大写金额会被误标成
        currency_symbol——已进化模式，去重后零产出=饥饿第二形态）。
        """
        from examples.crew.probe import synthesize_probes

        cases = synthesize_probes(
            evolved=set(self._evolved), existing_ids=self._probe_ids,
        )
        self._last_probe_pairs = [(c.inv, c.payload) for c in cases]
        probe_results: list[SolveResult] = []
        for case in cases:
            r = await self._solve_one(case.inv, case.payload)
            if not r.passed:
                r.error_tags = [case.mode]
            probe_results.append(r)
        m.probe_total = len(probe_results)
        failures: dict[str, int] = {}
        for r in probe_results:
            if not r.passed:
                for mode in r.error_tags:
                    failures[mode] = failures.get(mode, 0) + 1
        m.probe_failures_by_mode = failures
        return probe_results

    def _plan_batches(
        self, batch: list[tuple[Invoice, dict]], n_waves: int,
        curriculum: bool,
    ) -> list[list[tuple[Invoice, dict]]]:
        if not curriculum:
            return [list(batch) for _ in range(n_waves)]
        from examples.crew.explore import curriculum_batches

        planned = curriculum_batches(total_waves=n_waves)
        planned[0] = list(batch)  # 传入批为 wave1（gap 注入从 wave2 起）
        return planned

    async def _learn_from(self, results: list[SolveResult]) -> None:
        """B 闭环：失败模式 → 祈使规则记忆（下轮检索渲染进上下文）。"""
        if self._memory is None:
            return
        from examples.crew.learning import reflect_rules

        failures = [r.error_tags for r in results if not r.passed]
        if failures:
            await self._memory.write(reflect_rules(failures))

    async def _solve_batch(
        self, batch: list[tuple[Invoice, dict]], *, pipeline: bool = False,
    ) -> list[SolveResult]:
        if pipeline:
            return [
                await self.solve_pipeline(inv, p) for inv, p in batch
            ]
        return [await self._solve_one(inv, p) for inv, p in batch]

    async def solve_pipeline(
        self, inv: Invoice, payload: dict
    ) -> SolveResult:
        """三阶段流水线（编排深化）：classify → extract → audit。

        audit 对大额票（≥800）打回 needs_review（合规复核语义）。
        """
        from agent_core.task import TaskSpec

        self._env.reset_for(inv)
        stages: list[str] = []
        steps = 0
        rejected = 0

        def _task(kind: str, goal: dict) -> TaskSpec:
            return TaskSpec(
                id=f"crew_{inv.invoice_id}:{kind}", kind=kind,
                goal=goal, input_schema={}, constraints={},
                acceptance_criteria=[{"check": kind}],
            )

        # 1) classify：预判脏模式（编排选 classifier；失败不静默）
        self._env.dispatch_count = 0
        self._env.stage_pass = False
        cls_states = await self._orchestrator.orchestrate(
            _task("classify", {"payload": payload}), self._agents,
        )
        if cls_states.get("classifier") != "succeeded":
            return SolveResult(
                passed=False, error_tags=["classify_failed"],
                steps=self._env.dispatch_count, stages=["classify"],
            )
        stages.append("classify")
        steps += self._env.dispatch_count

        # 2) extract：分类预判传入 goal（编排选 extractor）
        self._env.dispatch_count = 0
        self._env.stage_pass = False
        goal = {"payload": payload,
                "classification": self._env.last_classification}
        if self._tool_first:
            goal["instruction"] = (
                "TOOL-FIRST 工具优先：提交字段前，若 tools 中存在与脏字段"
                "对应的归一化技能（normalize_*/correct_*），必须先调用该"
                "技能取得输出，再以技能输出构造 fields 提交——禁止自行心算。"
            )
        states = await self._orchestrator.orchestrate(
            _task("invoice_extract", goal), self._agents,
        )
        stages.append("extract")
        steps += self._env.dispatch_count
        rejected += self._env.rejected_submits
        extract_ok = (
            states.get("extractor") == "succeeded" and self._env.last_pass
        )

        # 3) audit：仅 extract 通过才复核；大额打回（编排选 auditor）
        needs_review = False
        if extract_ok:
            self._env.dispatch_count = 0
            self._env.stage_pass = False
            aud_states = await self._orchestrator.orchestrate(
                _task("audit", {"payload": payload, "extracted_ok": True}),
                self._agents,
            )
            if aud_states.get("auditor") != "succeeded":
                return SolveResult(
                    passed=False, error_tags=["audit_failed"],
                    steps=steps + self._env.dispatch_count,
                    stages=stages + ["audit"],
                )
            stages.append("audit")
            steps += self._env.dispatch_count
            needs_review = bool(self._env.last_audit.get("needs_review"))

        return SolveResult(
            passed=extract_ok, error_tags=list(self._env.last_errors),
            steps=steps, rejected_submits=rejected, stages=stages,
            needs_review=needs_review,
        )

    async def _solve_one(self, inv: Invoice, payload: dict) -> SolveResult:
        """单票据经真编排（Orchestrator 按 kind=invoice_extract 选 extractor）。"""
        from agent_core.task import TaskSpec

        self._env.reset_for(inv)
        goal: dict = {"payload": payload}
        if self._tool_first:
            goal["instruction"] = (
                "TOOL-FIRST 工具优先：提交字段前，若 tools 中存在与脏字段"
                "对应的归一化技能（normalize_*/correct_*），必须先调用该"
                "技能取得输出，再以技能输出构造 fields 提交——禁止自行心算。"
            )
        task = TaskSpec(
            id=f"crew_{inv.invoice_id}", kind="invoice_extract",
            goal=goal, input_schema={}, constraints={},
            acceptance_criteria=[{"check": "fields_match"}],
        )
        states = await self._orchestrator.orchestrate(task, self._agents)
        state = states.get("extractor", "failed")
        return SolveResult(
            passed=state == "succeeded" and self._env.last_pass,
            error_tags=list(self._env.last_errors),
            steps=self._env.dispatch_count,
        )

    async def _retry_failures(
        self, batch: list[tuple[Invoice, dict]], results: list[SolveResult]
    ) -> int:
        """进化后对失败票重派（同编排路径）——返回救回数。"""
        recovered = 0
        for (inv, payload), r in zip(batch, results, strict=True):
            if r.passed:
                continue
            retry = await self._solve_one(inv, payload)
            if retry.passed:
                recovered += 1
        return recovered

    async def _evolve_from(self, results: list[SolveResult]) -> None:
        """探索→进化：失败模式即 gap；每个未进化模式产一个技能（真门禁）。"""
        for r in results:
            for mode in r.error_tags:
                if mode in self._evolved:
                    continue
                self._generation += 1
                ok = await register_skill_for_mode(
                    self._env, mode, workspace=self._workspace,
                    generation=self._generation,
                )
                if ok:
                    self._evolved.add(mode)

    def _metrics(
        self, wave: int, results: list[SolveResult]
    ) -> WaveMetrics:
        passed = sum(1 for r in results if r.passed)
        failures: dict[str, int] = {}
        for r in results:
            if not r.passed:
                for mode in r.error_tags:
                    failures[mode] = failures.get(mode, 0) + 1
        skills = sum(
            1 for d in self._env.registry.discover({})
            if d.tags and "evolved" in d.tags
        )
        return WaveMetrics(
            wave=wave, total=len(results), passed=passed,
            success_rate=passed / len(results) if results else 0.0,
            avg_steps=(
                sum(r.steps for r in results) / len(results)
                if results else 0.0
            ),
            skills=skills, failures_by_mode=failures,
            total_rejected=sum(r.rejected_submits for r in results),
            needs_review_count=sum(
                1 for r in results if r.needs_review),
        )


def make_crew(
    env: CrewEnv, *, workspace: Path, memory=None,
    tool_first: bool = False,
) -> InvoiceOpsCrew:
    return InvoiceOpsCrew(
        env, workspace=workspace, memory=memory, tool_first=tool_first,
    )

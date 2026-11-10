"""DocumentAgentApp — 组装 Core + 记忆 + 探索的文档工作台应用（T22）。

工具链有派生状态：convert_format 的产物被后续 export/extract 消费——
这是应用层的环境状态（简单版 EnvironmentAdapter 语义）。
"""

from __future__ import annotations

from pathlib import Path

from agent_core.capability import CapabilityDescriptor, CapabilityType, InMemoryCapabilityRegistry
from agent_core.evidence import SqliteEvidenceStore
from agent_core.memory.retrieve import SimpleRetriever, TokenBudgetAssembler
from agent_core.memory.store import SqliteMemoryStore
from agent_core.memory.writer import DefaultConsolidator, DefaultMemoryWriter, VersionedPublisher
from agent_core.models import ModelAdapter
from agent_core.policy import DefaultPolicyEngine
from agent_core.run import RunHandle
from agent_core.runtime.driver import AgentSpec, VerificationResult
from agent_core.runtime.eventlog import SqliteEventLog
from agent_core.runtime.runtime import AgentRuntime
from agent_core.runtime.statestore import SqliteStateStore
from agent_core.task import TaskSpec
from agent_explore.controller import ExplorationController, ExplorationPolicy, ExplorationReport
from agent_explore.curriculum import LLMCurriculumGenerator
from agent_explore.curriculum_memory import DefaultAssembler
from agent_explore.gaps import SqliteGapLedger

from examples.docworkbench import tools as wb
from examples.docworkbench.acceptance import run_acceptance
from examples.docworkbench.corpus import Corpus, CorpusPart

DIM_TOOL = {
    "export": "export_document",
    "extract": "extract_fields",
    "convert": "convert_format",
    "validate": "validate_form",
}

ENV_VERSION = "workbench-1"


class WorkbenchVerifier:
    """从证据链重建交付物，对照生成器真值做程序化验收。"""

    def __init__(self, corpus: Corpus, evidence: SqliteEvidenceStore) -> None:
        self._corpus = corpus
        self._evidence = evidence

    async def verify_action(self, request, result) -> VerificationResult:
        return VerificationResult(passed=result.status.value == "succeeded")

    async def verify_task(self, task, evidence_refs) -> VerificationResult:
        corpus_task = next((t for t in self._corpus.tasks if t["id"] == task.spec_id), None)
        if corpus_task is None:
            return VerificationResult(
                passed=False,
                checks=[{"check": "task_known", "detail": f"任务 {task.spec_id} 不在语料中"}],
            )
        wanted_tool = DIM_TOOL[corpus_task["dimension"]]
        result: dict | None = None
        for ref in evidence_refs:
            lineage = await self._evidence.trace_lineage(ref)
            for obs in lineage["observations"]:
                summary = obs["state_summary"]
                if (
                    summary.get("capability_id") == wanted_tool
                    and summary.get("status") == "succeeded"
                ):
                    result = summary.get("output")  # 取最后一个匹配（最新交付）
        if result is None:
            return VerificationResult(
                passed=False,
                checks=[{"check": "result_exists", "detail": f"无 {wanted_tool} 的成功证据"}],
                evidence_refs=list(evidence_refs),
            )
        report = run_acceptance(corpus_task["id"], result, self._corpus)
        return VerificationResult(
            passed=report.passed, checks=report.checks, evidence_refs=list(evidence_refs)
        )


class DocumentAgentApp:
    def __init__(
        self,
        *,
        gateway,
        registry: InMemoryCapabilityRegistry,
        runtime: AgentRuntime,
        controller: ExplorationController,
        corpus: Corpus,
        memory_store: SqliteMemoryStore,
        writer: DefaultMemoryWriter,
        publisher: VersionedPublisher,
        evidence: SqliteEvidenceStore,
        practice_part: CorpusPart | None,
        derived: dict[str, dict],
        model_counter=None,
    ) -> None:
        self._gateway = gateway
        self._runtime = runtime
        self._controller = controller
        self._corpus = corpus
        self._memory_store = memory_store
        self._writer = writer
        self._publisher = publisher
        self._evidence = evidence
        self._derived = derived
        self._practice_part = practice_part
        self._model_counter = model_counter
        self._agent = AgentSpec(id="doc_agent", role="document worker", model_id="m-app")

    @property
    def model_counter(self):
        """真实调用计数器（CountingAdapter）——实验两臂的统一预算口径（§6.2）。"""
        return self._model_counter

    @property
    def practice_task_ids(self) -> set[str]:
        if self._practice_part is None:
            return {t["id"] for t in self._corpus.tasks}
        return {t["id"] for t in self._practice_part.tasks}

    async def solve(self, corpus_task: dict) -> RunHandle:
        spec = TaskSpec(
            id=corpus_task["id"],
            kind=f"wb:{corpus_task['dimension']}",
            goal={"what": corpus_task["goal"], "doc_id": corpus_task["doc_id"]},
            input_schema={},
            constraints={},
            acceptance_criteria=[{"check": "workbench_programmatic"}],
        )
        return await self._runtime.submit(spec, self._agent)

    async def explore(self, policy: ExplorationPolicy | None = None) -> ExplorationReport:
        policy = policy or ExplorationPolicy(mode="broad", max_waves=1)
        target = TaskSpec(
            id="workbench_target",
            kind="wb:mixed",
            goal={"what": "掌握文档工作台全部工具约束"},
            input_schema={},
            constraints={},
            acceptance_criteria=[{"check": "exploration"}],
        )
        verifier = WorkbenchVerifier(self._corpus, self._evidence)
        report = await self._controller.explore(
            target,
            policy,
            verifier=verifier,
            app_context={
                "runtime": self._runtime,
                "agent": self._agent,
                "reflector": self._reflector,
                "environment_version": ENV_VERSION,
                "practice_pool": self._practice_pool(),
                "model_counter": self._model_counter,
            },
        )
        # 探索产出的候选经验：验证通过后发布（对 solve 生效）
        await self._publish_candidates()
        return report

    def _practice_pool(self) -> list[dict]:
        ids = self.practice_task_ids
        return [
            {"id": t["id"], "dimension": t["dimension"], "doc_id": t["doc_id"], "goal": t["goal"]}
            for t in self._corpus.tasks
            if t["id"] in ids
        ]

    async def _reflector(self, task, outcome):
        if outcome.status != "passed" or not outcome.evidence_refs:
            return []  # 无真实证据引用：不产生记忆（宁可不学，不可编造）
        dim = task.id.split("_", 1)[0]
        statement = {
            "export": "xlsx 导出含图表扫描页前必须 convert_format(dpi=200)，否则丢页",
            "convert": "扫描件 convert_format 须 dpi>=150，否则丢字段",
            "extract": "双栏版式 extract_fields 必须传 region=left，否则串栏",
            "validate": "invoice_no 以 TMP 开头时跳过校验和恒有效",
        }.get(dim, f"{task.title} 有可复用操作路径")
        return [
            {
                "kind": "rule",
                "content": {
                    "statement": statement,
                    "claims": ["constraint_learned"],
                    "evidence_checked": ["constraint_learned"],
                },
                "applicability": {"app": "workbench", "env_version": ENV_VERSION},
                "evidence_refs": outcome.evidence_refs,
                "environment_version": ENV_VERSION,
            }
        ]

    async def _publish_candidates(self) -> None:
        """发布门：陈述须与工作台真值一致（环境级验收），否则保持 CANDIDATE。"""
        from examples.docworkbench.audit import statement_matches_truth

        rules = await self._memory_store.query("rules", {})
        for r in rules:
            if r.verification.value != "candidate" or not r.evidence_refs:
                continue
            statement = str(r.content.get("statement", ""))
            gate_ok = statement_matches_truth(statement)
            try:
                await self._publisher.publish(
                    r.id,
                    verifier_report={
                        "source": "exploration",
                        "app": "workbench",
                        "passed": gate_ok,
                        "checks": [{"check": "statement_matches_truth", "ok": gate_ok}],
                    },
                )
            except Exception:
                pass  # 发布被拒：保持 CANDIDATE，等待反例/复审


def _bind_tools(
    registry: InMemoryCapabilityRegistry, corpus: Corpus, derived: dict[str, dict]
) -> None:
    def doc_of(doc_id: str) -> dict:
        # 派生状态优先：转换后的文档供后续工具消费
        if doc_id in derived:
            return derived[doc_id]
        return next(d for d in corpus.documents if d["id"] == doc_id)

    def tool(id_, fn, side_effect="read"):
        async def handler(args):
            return fn(doc_of(args["doc_id"]), **{k: v for k, v in args.items() if k != "doc_id"})

        handler.__name__ = f"wb_{id_}"
        registry.bind(
            CapabilityDescriptor(
                id=id_,
                type=CapabilityType.TOOL,
                name=id_,
                input_schema={"doc_id": "str"},
                output_schema={},
                side_effect_level=side_effect,
                auth_scope=[],
                tags=["workbench"],
            ),
            handler,
        )

    tool("parse_document", wb.parse_document)
    tool("extract_fields", wb.extract_fields)
    tool("convert_format", wb.convert_format, side_effect="write")
    tool("export_document", wb.export_document, side_effect="write")
    tool("validate_form", wb.validate_form)

    # convert 产生派生文档，供 export/extract 消费——M8：公有 rebind 覆盖绑定
    original_convert = wb.convert_format

    async def convert_handler(args):
        doc = doc_of(args["doc_id"])
        converted = original_convert(doc, dpi=args.get("dpi", 200))
        derived[args["doc_id"]] = converted
        return {"lossy": converted.get("lossy", False), "dpi": converted.get("dpi"),
                "export_ready": converted.get("export_ready", False), "doc_id": args["doc_id"]}

    registry.rebind(
        CapabilityDescriptor(
            id="convert_format",
            type=CapabilityType.TOOL,
            name="convert_format",
            input_schema={"doc_id": "str"},
            output_schema={},
            side_effect_level="write",
            auth_scope=[],
            tags=["workbench"],
        ),
        convert_handler,
    )


def build_app(
    *,
    model_adapter: ModelAdapter,
    db_path: Path | str,
    blob_dir: Path | str,
    corpus: Corpus,
    practice_part: CorpusPart | None = None,
    verifier=None,
) -> DocumentAgentApp:
    from agent_core.models import CountingAdapter, ModelGateway

    # V2 §6.2：物理适配器包计数层——gateway 上的一切调用（solve/课程/reflector）都被计量
    model_counter = CountingAdapter(model_adapter)
    gateway = ModelGateway()
    gateway.register(model_counter)

    registry = InMemoryCapabilityRegistry()
    derived: dict[str, dict] = {}
    _bind_tools(registry, corpus, derived)

    event_log = SqliteEventLog(Path(db_path).with_suffix(".log.db"))
    state_store = SqliteStateStore(Path(db_path).with_suffix(".state.db"))
    evidence = SqliteEvidenceStore(Path(db_path).with_suffix(".ev.db"), blob_dir)
    memory_store = SqliteMemoryStore(Path(db_path).with_suffix(".mem.db"))
    gap_ledger = SqliteGapLedger(Path(db_path).with_suffix(".gaps.db"))

    runtime_verifier = verifier if verifier is not None else WorkbenchVerifier(corpus, evidence)
    retriever = SimpleRetriever(memory_store)
    assembler = TokenBudgetAssembler()

    runtime = AgentRuntime(
        gateway=gateway,
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        event_log=event_log,
        state_store=state_store,
        evidence_store=evidence,
        verifier=runtime_verifier,
        memory_retriever=retriever,
        context_assembler=assembler,
        environment_version=ENV_VERSION,
    )

    controller = ExplorationController(
        curriculum=LLMCurriculumGenerator(gateway=gateway, model_id="m-app"),
        assembler=DefaultAssembler(),
        writer=DefaultMemoryWriter(memory_store),
        consolidator=DefaultConsolidator(memory_store),
        memory_store=memory_store,
        gap_ledger=gap_ledger,
    )

    return DocumentAgentApp(
        gateway=gateway,
        registry=registry,
        runtime=runtime,
        controller=controller,
        corpus=corpus,
        memory_store=memory_store,
        writer=DefaultMemoryWriter(memory_store),
        publisher=VersionedPublisher(memory_store),
        evidence=evidence,
        practice_part=practice_part,
        derived=derived,
        model_counter=model_counter,
    )

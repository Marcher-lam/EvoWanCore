"""T30 V2 验收套件：汇总断言五大支柱（spec §6.6 / Review Focus 全覆盖）。"""

import json
from pathlib import Path

import pytest
from agent_adapters.mcp_server import MCPServerExporter
from agent_core.capability import (
    CapabilityDescriptor,
    CapabilityType,
    InMemoryCapabilityRegistry,
)
from agent_core.memory.record import MemoryKind, MemoryRecord, VerificationState
from agent_core.memory.retrieve import SimpleRetriever
from agent_core.memory.store import NS_RULES, SqliteMemoryStore
from agent_core.policy import DefaultPolicyEngine
from agent_core.runtime.executor import CapabilityExecutor
from examples.docworkbench.corpus import split_corpus
from examples.docworkbench.fake_vlm import WorkbenchFakeVLM
from examples.docworkbench.generator import generate_corpus
from examples.docworkbench.tools import convert_format


async def test_gate_guard_rejects_heldout(tmp_path: Path) -> None:
    """支柱 1（RF#1）：评测门守卫拒绝保留集任务。"""
    from agent_core.models import FakeModelAdapter, Modality, ModelCapabilities
    from examples.docworkbench.app import build_app

    corpus = generate_corpus(seed=3, size=8, difficulty=0.4)
    practice, heldout = split_corpus(corpus, practice_ratio=0.5)
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}), []
    )
    app = build_app(
        model_adapter=fake, db_path=tmp_path / "a.db", blob_dir=tmp_path / "b",
        corpus=corpus, practice_part=practice,
    )
    poisoned = {t["id"] for t in heldout.tasks} | app.practice_task_ids
    with pytest.raises(ValueError, match="保留集"):
        app._gate_tasks(app.practice_task_ids, poisoned, dimension="convert")  # noqa: SLF001


async def test_candidate_invisible_by_default(tmp_path: Path) -> None:
    """支柱 2（RF#2）：CANDIDATE 默认不进执行上下文。"""
    store = SqliteMemoryStore(tmp_path / "mem.db")
    await store.put(
        MemoryRecord(
            id="cand_1", kind=MemoryKind.RULE, namespace=NS_RULES,
            content={"statement": "候选规则"}, evidence_refs=["e1"],
            verification=VerificationState.CANDIDATE,
        )
    )
    got = await SimpleRetriever(store).retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": None, "keywords": []}, limit=10,
    )
    assert [r.id for r in got] == [], "CANDIDATE 默认必须排除"
    got2 = await SimpleRetriever(store, include_candidates=True).retrieve(
        scope={"namespaces": [NS_RULES]},
        task_context={"environment_version": None, "keywords": []}, limit=10,
    )
    assert [r.id for r in got2] == ["cand_1"]


async def test_perception_constraint_learnable() -> None:
    """支柱 3（RF#3）：感知陷阱可学——naive 读错 → convert 后全对。"""
    corpus = generate_corpus(seed=5, size=8, difficulty=0.8)
    doc = next(
        d
        for d in corpus.documents
        if any(p.get("contrast", 1.0) < 0.5 for p in d["pages"])
    )
    truth = {f["name"]: f["value"] for f in doc["fields"]}
    vlm = WorkbenchFakeVLM(corpus, derived={})

    naive = vlm._read_fields(doc)  # noqa: SLF001 — 感知行为单元钉
    assert naive != truth, "低对比度直读必须错（约束存在）"

    converted = convert_format(doc, dpi=200)
    assert all(p["contrast"] == 1.0 for p in converted["pages"])
    assert vlm._read_fields(converted) == truth, "convert 后必须全对（约束可解除）"


async def test_mcp_irreversible_rejected() -> None:
    """支柱 4（RF#4）：MCP 不可逆能力不出现在暴露面且逐调用被拒。"""
    registry = InMemoryCapabilityRegistry()

    async def boom(args):  # noqa: ARG001
        return {"destroyed": True}

    registry.bind(
        CapabilityDescriptor(
            id="doom", type=CapabilityType.TOOL, name="doom",
            input_schema={}, side_effect_level="irreversible", auth_scope=[],
        ),
        boom,
    )
    exporter = MCPServerExporter(
        registry=registry, policy_engine=DefaultPolicyEngine(),
        executor=CapabilityExecutor(registry=registry, skill_runner=None),
    )
    assert exporter.exportable() == [], "irreversible 不得暴露"
    with pytest.raises(PermissionError):
        await exporter.call_exported("doom", {})


def test_north_star_thresholds() -> None:
    """支柱 5：北极星报告自动判定——读最新 v3 json 断言三大阈值。"""
    report_path = Path("reports/exp_v3.json")
    if not report_path.exists():  # 报告为 gitignored 产物：缺席时跳过（CI 场景）
        pytest.skip("reports/exp_v3.json 不存在（实验产物未生成）")
    r = json.loads(report_path.read_text())
    explore = r["arms"]["explore"]
    assert r["comparable"] is True, "两臂预算必须可比"
    assert explore["per_dimension"]["extract"]["mean"] >= 0.67, (
        "北极星：explore extract ≥ 0.67"
    )
    # 红线由审计判定（workdirs 探测；缺席跳过）
    workdirs = Path("reports/workdirs")
    if workdirs.exists():
        import asyncio

        from examples.docworkbench.audit import audit_promoted

        async def _audit() -> float:
            total = wrong = 0
            for seed_dir in sorted(workdirs.glob("seed*")):
                mem = seed_dir / "explore" / "app.mem.db"
                if not mem.exists():
                    continue
                rep = await audit_promoted(SqliteMemoryStore(mem))
                total += rep.total
                wrong += rep.error_count
            return wrong / total if total else 0.0

        assert asyncio.run(_audit()) == 0.0, "红线：错误经验晋级率 = 0"

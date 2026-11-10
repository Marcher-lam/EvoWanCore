"""T23 同预算对照实验 + 记忆审计。"""

import json as _json
from pathlib import Path

import pytest
from agent_core.memory.record import MemoryKind
from agent_core.memory.store import SqliteMemoryStore
from agent_core.memory.writer import DefaultMemoryWriter, VersionedPublisher
from agent_core.models import InferResponse
from examples.docworkbench.acceptance import AuditReport
from examples.docworkbench.audit import audit_promoted
from examples.docworkbench.corpus import split_corpus
from examples.docworkbench.experiment import ExperimentConfig, run_experiment
from examples.docworkbench.generator import generate_corpus


class ForeverAdapter:
    """结构化请求(课程出题)返回合法单任务波次；普通请求返回固定文本。"""

    def __init__(self, response: InferResponse, plan_task_id: str = "pt_x") -> None:
        self._response = response
        self._plan_task_id = plan_task_id

    def capabilities(self):
        from agent_core.models import Modality, ModelCapabilities

        return ModelCapabilities(model_id="m-app", inputs={Modality.TEXT})

    async def infer(self, request):
        if request.structured_output is not None:
            return InferResponse(
                text=_json.dumps(
                    {
                        "wave": {
                            "shared_memory_snapshot_id": "x",
                            "task_ids": [],
                            "parallelism": 1,
                        },
                        "tasks": [
                            {
                                "id": self._plan_task_id,
                                "family": "prerequisite",
                                "title": "练习",
                                "objective": {},
                                "acceptance": {
                                    "verifier_profile": "p",
                                    "required_evidence": [],
                                    "pass_conditions": [],
                                    "fail_conditions": [],
                                },
                            }
                        ],
                    }
                )
            )
        return self._response


def _config(tmp_path: Path, seeds=(11, 12), difficulty=0.3):
    def corpus_factory(seed: int):
        return generate_corpus(seed=seed, size=8, difficulty=difficulty)

    def adapter_factory(seed: int):
        # 评测响应：交白卷（无工具调用）→ 任务 FAILED，分数 0；统计结构仍完整
        # 课程请求：返回单练习任务波次（任务 id 取自该种子语料的练习池）
        corpus = corpus_factory(seed)
        practice, _heldout = split_corpus(corpus, 0.5)
        task_id = (practice.tasks or corpus.tasks)[0]["id"]
        return ForeverAdapter(InferResponse(text="give up"), plan_task_id=task_id)

    from agent_explore.curriculum import ExplorationBudget

    return ExperimentConfig(
        seeds=list(seeds),
        budget_w=ExplorationBudget(max_actions=4),
        practice_ratio=0.5,
        corpus_factory=corpus_factory,
        adapter_factory=adapter_factory,
        workdir=tmp_path,
    )


async def test_two_arms_equal_budget(tmp_path: Path) -> None:
    report = await run_experiment(_config(tmp_path))
    explore_spent = report.arms["explore"].budget_spent
    retry_spent = report.arms["retry"].budget_spent
    n_seeds = 2
    assert explore_spent["actions"] == retry_spent["actions"] == 4 * n_seeds  # 每种子 4
    # V2 §6.2 真实调用计数：action 门预算下两臂各自计量真实调用（非等量断言）；
    # 探索臂含课程规划开销 ≥ 重试臂（纯 solve 调用）。comparable 的调用口径 = T16。
    assert retry_spent["tokens"] == 4 * n_seeds  # 每次 solve 恰 1 次模型调用
    assert explore_spent["tokens"] == 8 * n_seeds  # 每波 plan+practice 各 1 次
    assert explore_spent["tokens"] >= retry_spent["tokens"] > 0


async def test_report_has_mean_and_variance_per_group(tmp_path: Path) -> None:
    report = await run_experiment(_config(tmp_path))
    for arm_name, arm in report.arms.items():
        assert arm.scores, f"{arm_name} 应有保留集分数"
        assert arm.mean >= 0.0
        assert arm.variance >= 0.0
        assert set(arm.per_dimension) == {"export", "extract", "convert", "validate"}
        for _dim, stats in arm.per_dimension.items():
            assert "mean" in stats and "variance" in stats
    assert report.seeds == [11, 12]
    assert report.comparable, "两臂预算相同才可比较"


async def test_audit_detects_wrong_memory(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    writer = DefaultMemoryWriter(store)
    publisher = VersionedPublisher(store)

    # 一条真规则（与 HIDDEN_CONSTRAINTS 一致）
    good = await writer.commit(
        await writer.propose(
            kind=MemoryKind.RULE,
            content={
                "statement": "xlsx 导出含图表扫描页前必须 convert_format(dpi=200)，否则丢页",
                "claims": ["c"], "evidence_checked": ["c"],
            },
            applicability={"app": "workbench"},
            evidence_refs=["evi_1"],
        )
    )
    await publisher.publish(good.id, verifier_report={"src": "explore"})

    # 一条错误规则（违背真值：让丢页"合法化"）
    bad = await writer.commit(
        await writer.propose(
            kind=MemoryKind.RULE,
            content={
                "statement": "xlsx 导出丢页属于正常行为，无需转换，直接导出即可",
                "claims": ["c"], "evidence_checked": ["c"],
            },
            applicability={"app": "workbench"},
            evidence_refs=["evi_2"],
        )
    )
    await publisher.publish(bad.id, verifier_report={"src": "explore"})

    corpus = generate_corpus(seed=3, size=4, difficulty=0.3)
    report = await audit_promoted(store, corpus)
    assert isinstance(report, AuditReport)
    assert report.total == 2
    assert report.error_count == 1
    assert 0.49 < report.error_promotion_rate < 0.51
    assert any("丢页" in w or "正常" in w for w in report.wrong_statements)


async def test_audit_clean_when_all_true(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    writer = DefaultMemoryWriter(store)
    publisher = VersionedPublisher(store)
    rec = await writer.commit(
        await writer.propose(
            kind=MemoryKind.RULE,
            content={
                "statement": "双栏版式 extract_fields 必须传 region=left，否则串栏",
                "claims": ["c"], "evidence_checked": ["c"],
            },
            applicability={"app": "workbench"},
            evidence_refs=["evi"],
        )
    )
    await publisher.publish(rec.id, verifier_report={})
    corpus = generate_corpus(seed=3, size=4, difficulty=0.3)
    report = await audit_promoted(store, corpus)
    assert report.error_promotion_rate == 0.0


async def test_default_factory_uses_env_provider(tmp_path: Path, monkeypatch) -> None:
    """环境配置了供应商 → 默认工厂产出真实 OpenAI 兼容适配器。"""
    import agent_adapters.openai_compat as oc
    from examples.docworkbench.experiment import _default_adapter_factory

    built: list[dict] = []

    class FakeReal:
        def __init__(self, **kw):
            built.append(kw)
            self.caps = kw["capabilities"]

        def capabilities(self):
            return self.caps

    monkeypatch.setenv("EWC_BASE_URL", "https://api.deepseek.test/v1")
    monkeypatch.setenv("EWC_API_KEY", "sk-env")
    monkeypatch.setenv("EWC_MODEL_CHAT", "ds-flash")
    orig = oc.OpenAICompatAdapter
    oc.OpenAICompatAdapter = FakeReal  # type: ignore[assignment]
    try:
        factory = _default_adapter_factory(
            lambda seed: generate_corpus(seed=1, size=4, difficulty=0.3), 0.5
        )
        adapter = factory(7)
        assert adapter is not None
        assert built[0]["base_url"] == "https://api.deepseek.test/v1"
        assert built[0]["api_key"] == "sk-env"
        assert built[0]["capabilities"].model_id == "ds-flash"
    finally:
        oc.OpenAICompatAdapter = orig  # type: ignore[assignment]


def test_arm_stats_per_dimension_covers_all_seeds() -> None:
    """修复回归：per_dimension 必须统计全部种子的任务，而非只映射末位种子。"""
    from examples.docworkbench.experiment import _arm_stats

    dims = ["export", "extract", "convert", "validate"]
    pairs = []
    for seed in range(3):  # 3 个种子，各 4 题；分数随种子变（seed2 全挂）
        for i, dim in enumerate(dims):
            pairs.append(
                ({"id": f"{dim}_{seed}_{i}", "dimension": dim}, 1.0 if seed < 2 else 0.0)
            )
    arm = _arm_stats("x", pairs, spent={}, attempts=12)
    # 每个维度应有 3 题分数（3 种子）：mean = 2/3，若只统计末位种子则恒为 0.0
    for dim in dims:
        assert arm.per_dimension[dim]["mean"] == pytest.approx(2 / 3), f"{dim} 应含 3 种子的分数"


# ---------- T15：结构化声明审计 + reviewer ----------


def test_audit_matches_structured_claim() -> None:
    """结构化声明按 claim 机器判定（§6.3）：action 对但 params 不满足 → 错误。"""
    from examples.docworkbench.audit import statement_matches_truth

    good = {
        "claim": {"dimension": "extract", "action": "extract_fields",
                  "params": {"region": "left"}},
        "statement": "双栏需 region=left",
    }
    assert statement_matches_truth(good) is True

    # action 命中 convert 约束但 dpi=100 < 150/200 → 错误；正确文本不救（结构化为准）
    bad_params = {
        "claim": {"dimension": "convert", "action": "convert_format",
                  "params": {"dpi": 100}},
        "statement": "扫描件 convert_format 须 dpi>=150，否则丢字段",
    }
    assert statement_matches_truth(bad_params) is False

    # 无 claim 的纯文本陈述 → 保持 V1 关键词匹配语义
    assert statement_matches_truth("双栏版式必须传 region=left，否则串栏") is True


async def test_audit_reviewer_overrides(tmp_path: Path) -> None:
    """reviewer（gateway 强模型复核）可推翻确定性匹配——awaitable 兼容。"""
    from agent_core.models import (
        FakeModelAdapter,
        InferResponse,
        Modality,
        ModelCapabilities,
        ModelGateway,
    )
    from examples.docworkbench.audit import audit_promoted, make_gateway_reviewer

    store = SqliteMemoryStore(tmp_path / "mem.db")
    writer = DefaultMemoryWriter(store)
    publisher = VersionedPublisher(store)
    rec = await writer.commit(
        await writer.propose(
            kind=MemoryKind.RULE,
            content={"statement": "双栏版式 extract_fields 必须传 region=left，否则串栏"},
            applicability={"app": "workbench"},
            evidence_refs=["evi"],
        )
    )
    await publisher.publish(rec.id, verifier_report={})

    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-aud", inputs={Modality.TEXT}),
        [InferResponse(text="NO — 该规则站不住")],  # 复核否定
    )
    gw = ModelGateway()
    gw.register(fake)
    reviewer = make_gateway_reviewer(gw, "m-aud")

    report = await audit_promoted(store, reviewer=reviewer)
    assert report.total == 1
    assert report.error_count == 1, "gateway 复核否定应推翻确定性匹配"


def test_audit_accepts_true_alternative_strategies() -> None:
    """T23 迭代 6：审计接受真值上成立的替代策略——(a) 非 xlsx 导出绕过图表丢页
    （lossy 分支仅 format==xlsx）；(b) 管线式 claim 的嵌入参数（*_dpi: 200）；
    (c) 真错（xlsx 直出）仍拒。红线口径=审计，审计须与工具真实因果一致。"""
    from examples.docworkbench.audit import statement_matches_truth

    # (a) format=json 绕过 xlsx 图表丢页——真策略
    alt_format = {
        "claim": {"dimension": "export", "action": "export_document",
                  "params": {"format": "json"}},
        "statement": "export with explicit non-xlsx format keeps all pages",
    }
    assert statement_matches_truth(alt_format) is True
    # (b) 管线式：参数名嵌入 dpi，值满足 >=200
    pipeline = {
        "claim": {"dimension": "export", "action": "export_document",
                  "params": {"format": "pdf", "preceded_by_convert_dpi": 200}},
        "statement": "管线：convert(dpi=200) 后导出",
    }
    assert statement_matches_truth(pipeline) is True
    # (c) xlsx 直出（真错）仍拒
    wrong = {
        "claim": {"dimension": "export", "action": "export_document",
                  "params": {"format": "xlsx"}},
        "statement": "xlsx direct export keeps all pages",
    }
    assert statement_matches_truth(wrong) is False


async def test_comparable_uses_call_count_when_w_in_calls(tmp_path: Path) -> None:
    """C1（终评审）：W 以真实调用计数计量时，comparable=两臂都打满同一 W（§6.2）。"""
    from agent_explore.curriculum import ExplorationBudget

    def corpus_factory(seed: int):
        return generate_corpus(seed=seed, size=8, difficulty=0.3)

    def adapter_factory(seed: int):
        corpus = corpus_factory(seed)
        practice, _heldout = split_corpus(corpus, 0.5)
        task_id = (practice.tasks or corpus.tasks)[0]["id"]
        return ForeverAdapter(InferResponse(text="give up"), plan_task_id=task_id)

    cfg = ExperimentConfig(
        seeds=[11],
        budget_w=ExplorationBudget(max_tokens=4, max_actions=12),  # W=4 次调用
        practice_ratio=0.5,
        corpus_factory=corpus_factory,
        adapter_factory=adapter_factory,
        workdir=tmp_path,
        max_waves=8,
    )
    report = await run_experiment(cfg)
    # 重试臂每次 solve 恰 1 次调用 → 4 次；探索臂每波 plan+practice 2 次 → 2 波=4 次
    assert report.arms["retry"].budget_spent["tokens"] >= 4
    assert report.arms["explore"].budget_spent["tokens"] >= 4
    assert report.comparable is True, "两臂打满同一调用预算 → comparable"

    cfg_capped = ExperimentConfig(
        seeds=[11],
        budget_w=ExplorationBudget(max_tokens=6, max_actions=12),
        practice_ratio=0.5,
        corpus_factory=corpus_factory,
        adapter_factory=adapter_factory,
        workdir=tmp_path / "capped",
        max_waves=1,  # 探索臂波次上限先于预算耗尽 → 未打满
    )
    capped = await run_experiment(cfg_capped)
    assert capped.comparable is False, "探索臂未打满调用预算（波次上限截断）→ 不可比"


def test_audit_embedded_match_requires_dimension_relevance() -> None:
    """I1（终评审）：嵌入参数匹配须与约束维度相关——跨维度巧合串名不算真策略。"""
    from examples.docworkbench.audit import statement_matches_truth

    # 对抗样例 1：validate 动作的 format 参数碰瓷 export 约束的 format 键 → 拒
    sneaky_format = {
        "claim": {"dimension": "validate", "action": "validate_form",
                  "params": {"output_format": "json"}},
    }
    assert statement_matches_truth(sneaky_format) is False
    # 对抗样例 2：parse 动作的 render_dpi 碰瓷 dpi 阈值 → 拒
    sneaky_dpi = {
        "claim": {"dimension": "extract", "action": "parse_document",
                  "params": {"render_dpi": 999}},
    }
    assert statement_matches_truth(sneaky_dpi) is False
    # 合法管线式（export 维度 + 嵌入 dpi）仍接受
    legit = {
        "claim": {"dimension": "export", "action": "export_document",
                  "params": {"format": "pdf", "preceded_by_convert_dpi": 200}},
    }
    assert statement_matches_truth(legit) is True

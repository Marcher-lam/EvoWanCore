"""T21 合成文档工作台：种子确定性、隐藏约束真实生效、分层切分、纯 Python 验收。"""

import random

from examples.docworkbench.acceptance import AcceptanceReport, run_acceptance
from examples.docworkbench.corpus import Corpus, split_corpus
from examples.docworkbench.generator import generate_corpus
from examples.docworkbench.tools import HIDDEN_CONSTRAINTS, export_document, extract_fields


def _corpus(seed: int = 7, size: int = 12) -> Corpus:
    return generate_corpus(seed=seed, size=size, difficulty=0.5)


def test_corpus_deterministic_by_seed() -> None:
    a = _corpus(seed=7)
    b = _corpus(seed=7)
    c = _corpus(seed=8)
    assert [t["id"] for t in a.tasks] == [t["id"] for t in b.tasks]
    assert a.documents == b.documents
    assert a.ground_truth.keys() == b.ground_truth.keys()
    assert [t["id"] for t in a.tasks] != [t["id"] for t in c.tasks] or a.documents != c.documents
    # 渲染资产存在且非空
    assert all(len(png) > 0 for png in a.assets.values())


def test_documents_have_capability_dims() -> None:
    corpus = _corpus()
    dims = {t["dimension"] for t in corpus.tasks}
    assert dims == {"export", "extract", "convert", "validate"}


def test_hidden_constraints_actually_bite() -> None:
    corpus = _corpus()
    # 约束 1：含图表扫描页的文档，xlsx 导出会静默丢页（除非先 convert_format(dpi=200)）
    doc = next(d for d in corpus.documents if d.get("has_chart_page"))
    assert doc, "语料必须包含带图表页的文档"
    naive = export_document(doc, format="xlsx")
    full_pages = len(doc["pages"])
    assert len(naive["exported_pages"]) < full_pages, "不知约束的朴素导出必须丢页"

    from examples.docworkbench.tools import convert_format

    prepared = convert_format(doc, dpi=200)
    good = export_document(prepared, format="xlsx")
    assert len(good["exported_pages"]) == full_pages

    # 约束 2：双栏版式不传 region，抽取结果被污染
    two_col = next(d for d in corpus.documents if d.get("layout") == "two_column")
    naive_fields = extract_fields(two_col)
    expected = {f["name"]: f["value"] for f in two_col["fields"]}
    assert naive_fields["fields"] != expected
    scoped = extract_fields(two_col, region="left")
    assert scoped["fields"] == expected


def test_hidden_constraints_table_is_auditable() -> None:
    # 隐藏约束集中在常量表（记忆审计对照真值用）
    assert len(HIDDEN_CONSTRAINTS) >= 3
    assert all({"dimension", "rule", "naive_fails"} <= set(c) for c in HIDDEN_CONSTRAINTS)


def test_split_balances_capability_dims() -> None:
    corpus = _corpus(size=24)
    practice, heldout = split_corpus(corpus, practice_ratio=0.5)
    for part in (practice, heldout):
        dims = [t["dimension"] for t in part.tasks]
        assert set(dims) == {"export", "extract", "convert", "validate"}
    # 同一任务不得同时出现在两边
    assert not {t["id"] for t in practice.tasks} & {t["id"] for t in heldout.tasks}
    assert len(practice.tasks) + len(heldout.tasks) == len(corpus.tasks)


def test_acceptance_is_pure_python() -> None:
    corpus = _corpus()
    task = corpus.tasks[0]
    truth = corpus.ground_truth[task["id"]]
    good_result = dict(truth["expected_result"])
    report = run_acceptance(task["id"], good_result, corpus)
    assert isinstance(report, AcceptanceReport)
    assert report.passed is True

    wrong_result = {"nope": True}
    report2 = run_acceptance(task["id"], wrong_result, corpus)
    assert report2.passed is False
    assert report2.checks  # 失败也要给出检查明细


def test_acceptance_detects_page_loss() -> None:
    corpus = _corpus()
    export_task = next(t for t in corpus.tasks if t["dimension"] == "export")
    doc = next(d for d in corpus.documents if d["id"] == export_task["doc_id"])
    naive = export_document(doc, format="xlsx")  # 可能丢页
    report = run_acceptance(export_task["id"], naive, corpus)
    if len(naive["exported_pages"]) < len(doc["pages"]):
        assert report.passed is False
        assert any(
            "页" in c.get("detail", "") or "page" in c.get("detail", "").lower()
            for c in report.checks
        )


def test_random_helper_isolated() -> None:
    # 生成器不得污染全局随机状态
    before = random.getstate()
    generate_corpus(seed=42, size=4, difficulty=0.3)
    assert random.getstate() == before

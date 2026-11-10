"""T22 文档 Agent 应用端到端：冷启动失败 → 探索沉淀记忆 → 同类任务通过。"""

import json
from pathlib import Path

import pytest
from agent_core.models import FakeModelAdapter, InferResponse, Modality, ModelCapabilities
from examples.docworkbench.app import build_app
from examples.docworkbench.corpus import split_corpus
from examples.docworkbench.generator import generate_corpus


def _tool_call(name: str, args: dict) -> InferResponse:
    return InferResponse(tool_calls=[{"id": name, "name": name, "arguments": args}])


def _practice_plan(task_id: str, title: str) -> str:
    return json.dumps(
        {
            "wave": {"shared_memory_snapshot_id": "x", "task_ids": [], "parallelism": 1},
            "tasks": [
                {
                    "id": task_id,
                    "family": "prerequisite",
                    "title": title,
                    "objective": {"what": title},
                    "acceptance": {
                        "verifier_profile": "workbench_programmatic",
                        "required_evidence": [],
                        "pass_conditions": [{"check": "done"}],
                        "fail_conditions": [],
                    },
                }
            ],
        }
    )


def _make_corpus():
    """选一个存在"带图表页的导出任务"的确定性种子。"""
    for seed in range(60):
        c = generate_corpus(seed=seed, size=12, difficulty=0.4)
        export_ok = [
            t for t in c.tasks
            if t["dimension"] == "export"
            and next(d for d in c.documents if d["id"] == t["doc_id"])["has_chart_page"]
        ]
        if export_ok:
            return c
    raise AssertionError("60 个种子内未找到带图表页的导出任务")


@pytest.fixture
def corpus():
    return _make_corpus()


def _doc(corpus, task):
    return next(d for d in corpus.documents if d["id"] == task["doc_id"])


async def test_explore_then_solve_beats_cold_solve(tmp_path: Path, corpus) -> None:
    # 选一个带图表页的导出任务（朴素 xlsx 导出必丢页）和一个转换练习任务
    export_task = next(
        t
        for t in corpus.tasks
        if t["dimension"] == "export" and _doc(corpus, t)["has_chart_page"]
    )
    convert_task = next(t for t in corpus.tasks if t["dimension"] == "convert")
    export_doc_id = export_task["doc_id"]

    responses = [
        # 1) 冷启动 solve：朴素导出（不知道隐藏约束）
        _tool_call("export_document", {"doc_id": export_doc_id, "format": "xlsx"}),
        InferResponse(text="done"),
        # 2) explore：课程出一道转换练习（命中隐藏约束）
        InferResponse(text=_practice_plan(convert_task["id"], "练习无损转换")),
        _tool_call("convert_format", {"doc_id": convert_task["doc_id"], "dpi": 200}),
        InferResponse(text="done"),
        # 3) 探索后 solve：先转换再导出（记忆规则被检索进上下文）
        _tool_call("convert_format", {"doc_id": export_doc_id, "dpi": 200}),
        _tool_call("export_document", {"doc_id": export_doc_id, "format": "xlsx"}),
        InferResponse(text="done"),
    ]

    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}, tools={"native_tool_call"}),
        responses,
    )
    app = build_app(
        model_adapter=fake,
        db_path=tmp_path / "app.db",
        blob_dir=tmp_path / "blobs",
        corpus=corpus,
    )

    cold = await app.solve(export_task)
    assert cold.state.value == "failed", "朴素导出必须因隐藏约束失败"

    report = await app.explore()
    assert report.tasks_passed == 1
    assert report.candidates_generated == 1

    warm = await app.solve(export_task)
    assert warm.state.value == "succeeded", "探索沉淀的记忆应让同类任务通过"

    # 记忆确实进入了第二轮 solve 的模型上下文
    solve_requests = [
        r
        for r in fake.recorded_requests
        if export_doc_id in " ".join(m["content"] for m in r.messages)
    ]
    assert solve_requests, "solve 请求应携带任务信息"
    last_solve = solve_requests[-1]
    all_content = " ".join(m["content"] for m in last_solve.messages)
    assert "convert_format" in all_content, "检索到的规则应注入模型上下文"


async def test_solve_without_exploration_on_easy_task(tmp_path: Path, corpus) -> None:
    """无隐藏约束陷阱的任务冷启动即可通过（对照组，防假阳性）。"""
    validate_task = next(t for t in corpus.tasks if t["dimension"] == "validate")
    doc = _doc(corpus, validate_task)
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}, tools={"native_tool_call"}),
        [
            _tool_call("validate_form", {"doc_id": doc["id"]}),
            InferResponse(text="done"),
        ],
    )
    app = build_app(
        model_adapter=fake,
        db_path=tmp_path / "app.db",
        blob_dir=tmp_path / "blobs",
        corpus=corpus,
    )
    run = await app.solve(validate_task)
    assert run.state.value == "succeeded"


def test_heldout_invisible_to_explore(tmp_path: Path, corpus) -> None:
    """切分后保留集任务不进入练习池（探索不可见）。"""
    practice, heldout = split_corpus(corpus, practice_ratio=0.5)
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}), []
    )
    app = build_app(
        model_adapter=fake,
        db_path=tmp_path / "app.db",
        blob_dir=tmp_path / "blobs",
        corpus=corpus,
        practice_part=practice,
    )
    assert app.practice_task_ids == {t["id"] for t in practice.tasks}
    assert not app.practice_task_ids & {t["id"] for t in heldout.tasks}

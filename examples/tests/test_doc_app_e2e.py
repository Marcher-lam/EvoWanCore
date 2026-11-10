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
    ]
    # 2.5) V2 T12 通用评测门：候选（convert 维度）过父子对比 + 回归
    #      with 臂 3 题 dpi=200 全过；parent 臂 3 题白卷；回归 2×2 白卷（等分无回归）
    for t in [t for t in corpus.tasks if t["dimension"] == "convert"][:3]:
        responses.append(_tool_call("convert_format", {"doc_id": t["doc_id"], "dpi": 200}))
        responses.append(InferResponse(text="done"))
    responses.extend(InferResponse(text="give up") for _ in range(3))
    responses.extend(InferResponse(text="give up") for _ in range(4))
    responses += [
        # 3) 探索后 solve：先转换再导出（已发布规则被检索进上下文）
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


async def test_publish_gate_blocks_wrong_and_unevidenced_rules(tmp_path: Path) -> None:
    """I1 回归：错误陈述不晋级；无真实证据不写记忆。"""
    corpus = _make_corpus()
    convert_task = next(t for t in corpus.tasks if t["dimension"] == "convert")
    plan = json.dumps(
        {
            "wave": {"shared_memory_snapshot_id": "x", "task_ids": [], "parallelism": 1},
            "tasks": [
                {
                    "id": convert_task["id"],
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
    # 1) 练习通过但 reflector 产出错误陈述 → 不晋级（保持 CANDIDATE）
    #    V2 T12 后发布走通用评测门：父子对比 solve 也需脚本（全白卷 → 不 improved → 拒）
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}, tools={"native_tool_call"}),
        [
            InferResponse(text=plan),
            InferResponse(
                tool_calls=[
                    {"id": "c", "name": "convert_format",
                     "arguments": {"doc_id": convert_task["doc_id"], "dpi": 200}}
                ]
            ),
            InferResponse(text="done"),
        ]
        + [InferResponse(text="give up")] * 10,  # 门评测：with3+parent3+回归4 全白卷
    )
    app = build_app(
        model_adapter=fake, db_path=tmp_path / "a.db", blob_dir=tmp_path / "b1", corpus=corpus
    )
    wrong_reflector = lambda task, outcome: [  # noqa: E731
        {
            "kind": "rule",
            "content": {
                "statement": "xlsx 导出丢页是正常行为，无需转换",
                "claims": ["c"], "evidence_checked": ["c"],
            },
            "applicability": {"app": "workbench"},
            "evidence_refs": outcome.evidence_refs,
        }
    ]
    app._reflector = wrong_reflector  # noqa: SLF001
    await app.explore()
    rules = await app._memory_store.query("rules", {})  # noqa: SLF001
    assert rules and rules[0].verification.value == "candidate", "错误陈述不得晋级"

    # 2) 练习通过但无证据引用 → 不写任何记忆
    fake2 = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}, tools={"native_tool_call"}),
        [
            InferResponse(text=plan),
            InferResponse(
                tool_calls=[
                    {"id": "c", "name": "convert_format",
                     "arguments": {"doc_id": convert_task["doc_id"], "dpi": 200}}
                ]
            ),
            InferResponse(text="done"),
        ],
    )
    app2 = build_app(
        model_adapter=fake2, db_path=tmp_path / "a2.db", blob_dir=tmp_path / "b2", corpus=corpus
    )
    app2._reflector = lambda task, outcome: [  # noqa: E731
        {
            "kind": "rule",
            "content": {
                "statement": "xlsx 导出前必须 convert_format(dpi=200)",
                "claims": ["c"], "evidence_checked": ["c"],
            },
            "applicability": {"app": "workbench"},
            "evidence_refs": [],  # 无真实证据
        }
    ]
    await app2.explore()
    rules2 = await app2._memory_store.query("rules", {})  # noqa: SLF001
    assert all(
        r.verification.value == "candidate" for r in rules2
    ), "无证据引用的记忆最多保持 CANDIDATE，不得晋级"


# ---------- T12：通用评测门接入发布路径 ----------


def _convert_tasks(corpus, n: int = 3) -> list[dict]:
    return [t for t in corpus.tasks if t["dimension"] == "convert"][:n]


def _gate_script_good_rule(corpus) -> list[InferResponse]:
    """门评测脚本：with 臂按规则 dpi=200 全过（tool+done×3）；
    parent 臂交白卷×3；回归 2 题×2 臂交白卷（等分 → 无回归违例）。"""
    responses: list[InferResponse] = []
    for t in _convert_tasks(corpus):
        responses.append(_tool_call("convert_format", {"doc_id": t["doc_id"], "dpi": 200}))
        responses.append(InferResponse(text="done"))
    responses.extend(InferResponse(text="give up") for _ in range(3))  # parent 臂
    responses.extend(InferResponse(text="give up") for _ in range(4))  # 回归
    return responses


_GATE_GIVEUPS = [InferResponse(text="give up")] * 10  # with/parent 各 3 + 回归 4 全白卷


async def test_good_rule_publishes_via_gate(tmp_path: Path, corpus) -> None:
    """真规则过通用评测门（父子对比 + 回归）→ VERIFIED + NS_RELEASES manifest。"""
    convert_task = next(t for t in corpus.tasks if t["dimension"] == "convert")
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}, tools={"native_tool_call"}),
        [InferResponse(text=_practice_plan(convert_task["id"], "练习无损转换"))]
        + [
            _tool_call("convert_format", {"doc_id": convert_task["doc_id"], "dpi": 200}),
            InferResponse(text="done"),
        ]
        + _gate_script_good_rule(corpus),
    )
    app = build_app(
        model_adapter=fake, db_path=tmp_path / "g.db", blob_dir=tmp_path / "gb", corpus=corpus
    )
    await app.explore()

    rules = await app._memory_store.query("rules", {})  # noqa: SLF001
    assert rules and rules[0].verification.value == "verified", "好规则应过门晋级"
    report = rules[0].content.get("verification_report", {})
    assert report.get("source") == "evolution_gate", "发布必须经由通用评测门"
    releases = await app._memory_store.query("releases", {})  # noqa: SLF001
    assert releases, "发布应写 NS_RELEASES 记录（manifest 持久化）"


async def test_gate_rejects_wrong_rule_via_eval(tmp_path: Path, corpus) -> None:
    """错误陈述 → 评测门拒绝（父子对比不 improved）→ 保持 CANDIDATE。"""
    convert_task = next(t for t in corpus.tasks if t["dimension"] == "convert")
    fake = FakeModelAdapter(
        ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}, tools={"native_tool_call"}),
        [InferResponse(text=_practice_plan(convert_task["id"], "练习"))]
        + [
            _tool_call("convert_format", {"doc_id": convert_task["doc_id"], "dpi": 200}),
            InferResponse(text="done"),
        ]
        + list(_GATE_GIVEUPS),
    )
    app = build_app(
        model_adapter=fake, db_path=tmp_path / "w.db", blob_dir=tmp_path / "wb", corpus=corpus
    )
    app._reflector = lambda task, outcome: [  # noqa: E731, SLF001
        {
            "kind": "rule",
            "content": {
                "statement": "xlsx 导出丢页是正常行为，无需转换",
                "claims": ["c"], "evidence_checked": ["c"],
            },
            "applicability": {"app": "workbench"},
            "evidence_refs": outcome.evidence_refs,
        }
    ]
    await app.explore()

    rules = await app._memory_store.query("rules", {})  # noqa: SLF001
    assert rules and rules[0].verification.value == "candidate", "评测门未过须保持 CANDIDATE"
    # 门确实运行过（对比臂产生了真实 solve 调用，而非旧真值门短路）
    assert len(fake.recorded_requests) > 10, "评测门应执行父子对比 solve"


async def test_gate_never_uses_heldout(tmp_path: Path, corpus) -> None:
    """红线守卫：门任务来源混入保留集 id → ValueError。"""
    practice, heldout = split_corpus(corpus, practice_ratio=0.5)
    fake = FakeModelAdapter(ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}), [])
    app = build_app(
        model_adapter=fake,
        db_path=tmp_path / "h.db",
        blob_dir=tmp_path / "hb",
        corpus=corpus,
        practice_part=practice,
    )
    poisoned = {t["id"] for t in heldout.tasks} | app.practice_task_ids  # 模拟污染
    with pytest.raises(ValueError, match="保留集"):
        app._gate_tasks(app.practice_task_ids, poisoned, dimension="convert")  # noqa: SLF001


async def test_gate_prefers_trap_tasks(tmp_path: Path, corpus) -> None:
    """T23 迭代：门 mini-batch 优先选带陷阱的文档（extract=低对比度扫描页）——
    规则只有在能区分对错的题上才可被验证。"""
    hard_corpus = generate_corpus(seed=5, size=12, difficulty=0.8)  # 感知陷阱存在
    practice, _heldout = split_corpus(hard_corpus, practice_ratio=0.5)
    fake = FakeModelAdapter(ModelCapabilities(model_id="m-app", inputs={Modality.TEXT}), [])
    app = build_app(
        model_adapter=fake, db_path=tmp_path / "trap.db", blob_dir=tmp_path / "tb",
        corpus=hard_corpus, practice_part=practice,
    )
    tasks = app._gate_tasks(app.practice_task_ids, set(), dimension="extract", limit=3)  # noqa: SLF001
    assert tasks, "extract 维度应有门任务"
    by_id = {t["id"]: t for t in hard_corpus.tasks}
    docs = {d["id"]: d for d in hard_corpus.documents}
    first_trap = any(
        any(p.get("contrast", 1.0) < 0.5 for p in docs[by_id[t.id]["doc_id"]]["pages"])
        for t in tasks
    )
    assert first_trap, "mini-batch 应含低对比度陷阱文档（可区分对错）"

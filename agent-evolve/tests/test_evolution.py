"""T15 候选评测与发布：门禁发布、回滚反向清单、同批父子对比。"""

import pytest
from agent_core.memory.store import SqliteMemoryStore
from agent_core.memory.writer import VersionedPublisher
from agent_core.task import TaskSpec
from agent_evolve.controller import (
    EvalReport,
    EvolutionController,
    ExperienceCandidate,
    PublishDenied,
)


@pytest.fixture
def controller(tmp_path):
    store = SqliteMemoryStore(tmp_path / "mem.db")
    return EvolutionController(memory_store=store, memory_publisher=VersionedPublisher(store))


def _task(i: int) -> TaskSpec:
    return TaskSpec(
        id=f"t{i}",
        kind="extract",
        goal={},
        input_schema={},
        constraints={},
        acceptance_criteria=[{"c": 1}],
    )


def _memory_candidate() -> ExperienceCandidate:
    return ExperienceCandidate(
        candidate_type="memory",
        payload={"memory_id": "mem_abc", "statement": "导出前需确认选区"},
        source_practice_ids=["p1"],
    )


async def test_eval_compares_parent_on_same_batch(controller) -> None:
    cid = controller.submit(_memory_candidate())

    async def runner(task: TaskSpec, *, with_candidate: bool) -> float:
        return (0.9 if task.id == "t0" else 0.8) if with_candidate else 0.5

    report = await controller.evaluate(cid, [_task(0), _task(1)], runner)
    assert isinstance(report, EvalReport)
    assert report.mini_batch_scores == [0.9, 0.8]
    assert report.parent_scores == [0.5, 0.5]
    assert report.improved is True
    assert report.regression_violations == []


async def test_eval_detects_regression(controller) -> None:
    cid = controller.submit(_memory_candidate())

    async def runner(task: TaskSpec, *, with_candidate: bool) -> float:
        # 候选在新批次更好，但在旧场景 t_old 上更差 → 回归
        if task.id == "t_old":
            return 0.2 if with_candidate else 0.9
        return 0.9 if with_candidate else 0.5

    report = await controller.evaluate(cid, [_task(0)], runner, regression_scenarios=[_task_old()])
    assert report.improved is True  # 新批次确实更好
    assert report.regression_violations == ["t_old"]  # 但旧能力退化被抓住


def _task_old() -> TaskSpec:
    return TaskSpec(
        id="t_old",
        kind="extract",
        goal={},
        input_schema={},
        constraints={},
        acceptance_criteria=[{"c": 1}],
    )


async def test_publish_requires_improvement_no_regression(controller) -> None:
    cid = controller.submit(_memory_candidate())
    bad = EvalReport(
        mini_batch_scores=[0.3], parent_scores=[0.5], improved=False, regression_violations=[]
    )
    with pytest.raises(PublishDenied, match="improved"):
        await controller.publish(cid, bad)

    regressed = EvalReport(
        mini_batch_scores=[0.9], parent_scores=[0.5], improved=True, regression_violations=["t_old"]
    )
    with pytest.raises(PublishDenied, match="regression"):
        await controller.publish(cid, regressed)

    good = EvalReport(
        mini_batch_scores=[0.9], parent_scores=[0.5], improved=True, regression_violations=[]
    )
    manifest = await controller.publish(cid, good)
    assert manifest.release_id.startswith("rel_")
    assert cid in manifest.candidates


async def test_rollback_generates_inverse_manifest(controller, tmp_path) -> None:
    # 先放一条会被发布的记忆，回滚时要走失效链
    from agent_core.memory.record import MemoryKind, MemoryRecord

    store = controller._memory_store
    rec = MemoryRecord(
        id="mem_abc",
        kind=MemoryKind.RULE,
        namespace="rules",
        content={"statement": "导出前需确认选区"},
        evidence_refs=["evi"],
    )
    await store.put(rec)

    cid = controller.submit(_memory_candidate())
    good = EvalReport(
        mini_batch_scores=[0.9], parent_scores=[0.5], improved=True, regression_violations=[]
    )
    manifest = await controller.publish(cid, good, memory_snapshot_id="snap_before")

    inverse = await controller.rollback(manifest.release_id, reason="线上发现反例")
    assert inverse.release_id != manifest.release_id
    assert inverse.rollback_of == manifest.release_id
    assert inverse.candidates == []
    # 发布的记忆被失效
    dead = await store.get("mem_abc")
    assert dead.lifecycle.value == "expired"
    assert "反例" in dead.content["invalidated_because"]


# ---------- T8（M10）：演化态持久化 ----------


class _MemEventLog:
    """测试用事件日志：append 进列表。"""

    def __init__(self) -> None:
        self.events: list = []

    async def append(self, event) -> None:
        self.events.append(event)


async def test_evolution_state_survives_restart(tmp_path) -> None:
    """M10：candidates/manifests 落 SQLite——新实例可 rollback 旧 manifest。"""
    from agent_core.memory.record import MemoryKind, MemoryRecord

    mem = SqliteMemoryStore(tmp_path / "mem.db")
    await mem.put(
        MemoryRecord(
            id="mem_abc",
            kind=MemoryKind.RULE,
            namespace="rules",
            content={"statement": "导出前需确认选区"},
            evidence_refs=["evi"],
        )
    )
    c1 = EvolutionController(
        memory_store=mem,
        memory_publisher=VersionedPublisher(mem),
        db_path=tmp_path / "evo.db",
    )
    cid = c1.submit(_memory_candidate())
    good = EvalReport(
        mini_batch_scores=[0.9], parent_scores=[0.5], improved=True, regression_violations=[]
    )
    manifest = await c1.publish(cid, good, memory_snapshot_id="snap_before")
    # publish 写 NS_RELEASES 记录
    releases = await mem.query("releases", {})
    assert any(r.id == manifest.release_id for r in releases), "发布应写 releases 命名空间记录"

    # 崩溃重启：新实例同一 db_path，旧 manifest 可回滚
    c2 = EvolutionController(
        memory_store=mem,
        memory_publisher=VersionedPublisher(mem),
        db_path=tmp_path / "evo.db",
    )
    inverse = await c2.rollback(manifest.release_id, reason="重启后回滚")
    assert inverse.rollback_of == manifest.release_id
    dead = await mem.get("mem_abc")
    assert dead.lifecycle.value == "expired"


async def test_publish_denied_writes_event(tmp_path) -> None:
    """M10：拒绝发布时写 PublishDenied 事件进（可选注入的）EventLog。"""
    mem = SqliteMemoryStore(tmp_path / "mem.db")
    log = _MemEventLog()
    controller = EvolutionController(
        memory_store=mem,
        memory_publisher=VersionedPublisher(mem),
        event_log=log,
    )
    cid = controller.submit(_memory_candidate())
    bad = EvalReport(
        mini_batch_scores=[0.3], parent_scores=[0.5], improved=False, regression_violations=[]
    )
    with pytest.raises(PublishDenied):
        await controller.publish(cid, bad)
    assert [e.type for e in log.events] == ["PublishDenied"]
    assert log.events[0].subject_id == cid

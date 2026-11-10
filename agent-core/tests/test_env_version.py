"""V2-T1 (M1)：环境版本参数化出 Core，记忆盖戳 environment_version。"""

from pathlib import Path

from agent_core.capability import InMemoryCapabilityRegistry
from agent_core.evidence import SqliteEvidenceStore
from agent_core.memory.record import MemoryKind
from agent_core.memory.store import SqliteMemoryStore
from agent_core.memory.writer import DefaultMemoryWriter
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


def _spec() -> TaskSpec:
    return TaskSpec(
        id="spec_t1", kind="parse", goal={"what": "x"}, input_schema={},
        constraints={}, acceptance_criteria=[{"c": 1}],
    )


class PassVerifier:
    async def verify_action(self, request, result):
        return VerificationResult(passed=True)

    async def verify_task(self, task, evidence_refs):
        return VerificationResult(passed=True)


def _runtime(tmp_path: Path, env_version: str, responses):
    registry = InMemoryCapabilityRegistry()
    gateway = ModelGateway()
    gateway.register(
        FakeModelAdapter(ModelCapabilities(model_id="m-x", inputs={Modality.TEXT}), responses)
    )
    return AgentRuntime(
        gateway=gateway,
        registry=registry,
        policy_engine=DefaultPolicyEngine(),
        event_log=SqliteEventLog(tmp_path / "l.db"),
        state_store=SqliteStateStore(tmp_path / "s.db"),
        evidence_store=SqliteEvidenceStore(tmp_path / "e.db", tmp_path / "b"),
        verifier=PassVerifier(),
        memory_retriever=None,
        environment_version=env_version,
    )


async def test_runtime_env_version_used_in_retrieval_context(tmp_path: Path) -> None:
    """构造 environment_version 必须进入检索上下文（而非 Core 内硬编码）。"""
    from agent_core.memory.record import MemoryKind, MemoryRecord
    from agent_core.memory.retrieve import SimpleRetriever

    store = SqliteMemoryStore(tmp_path / "m.db")
    rec = MemoryRecord(
        kind=MemoryKind.RULE, namespace="rules",
        content={"statement": "规则"}, evidence_refs=["e"], environment_version="wb-9",
    )
    await store.put(rec)
    retriever = SimpleRetriever(store)

    runtime = _runtime(tmp_path, "wb-9", [InferResponse(text="done")])
    runtime._memory_retriever = retriever  # noqa: SLF001 — 注入以观测检索上下文
    run = await runtime.submit(_spec(), AgentSpec(id="a", role="w", model_id="m-x"))
    assert run.state.value == "succeeded"


def test_writer_stamps_env_version() -> None:
    import asyncio

    async def main():
        import tempfile
        store = SqliteMemoryStore(Path(tempfile.mkdtemp()) / "m.db")
        writer = DefaultMemoryWriter(store)
        rec = await writer.propose(
            kind=MemoryKind.RULE,
            content={"statement": "x"},
            applicability={},
            evidence_refs=["e"],
            environment_version="wb-9",
        )
        assert rec.environment_version == "wb-9"

    asyncio.run(main())


def test_runtime_default_env_version_is_unspecified(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "unspecified", [])
    assert runtime._environment_version == "unspecified"  # noqa: SLF001
    assert "workbench" not in runtime._environment_version  # M1：业务常量出 Core

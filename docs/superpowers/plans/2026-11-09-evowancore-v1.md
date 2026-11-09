# EvoWanCore V1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端到端建成 EvoWanCore V1——探索/自进化闭环（BRS/DRS + 课程 + 候选记忆晋级）在合成文档工作台上验证"同预算探索 ≥ 重试对照"，外加实时语音 Omni 第二切片。

**Architecture:** 机制/策略分离的 monorepo：`agent-core`（契约+状态机+事件+运行时+记忆）、`agent-explore`（课程与探索）、`agent-evolve`（候选评测发布）、`agent-adapters`（模型适配）、`examples`（文档工作台测试床+实验、实时语音）。walking skeleton：契约按完整形状定义，只实现闭环拉出来的最小子集。

**Tech Stack:** Python 3.11+ / asyncio（TaskGroup）/ Pydantic v2 / typing.Protocol / uv workspace / pytest + pytest-asyncio / SQLite（aiosqlite）+ 本地文件 / Pillow（合成文档渲染）/ websockets（Realtime 客户端）。禁止引入 langchain/llamaindex 等编排框架。

**Spec:** `docs/superpowers/specs/2026-11-09-evowancore-v1-design.md`（本计划从该文档论证；执行者须同时读两份）

## Global Constraints

- Python `>=3.11`；所有异步代码 asyncio 原生，禁用阻塞调用出现在 async 路径
- 契约一律 Pydantic v2 BaseModel（可 `model_json_schema()` 导出）；扩展点一律 `typing.Protocol`
- 存储仅 SQLite + 本地文件系统；Asset 原始媒体永不覆盖（内容寻址：sha256 命名）
- 候选代码（技能脚本）一律子进程执行，禁止进程内 eval/import
- 验收证据来自环境/程序检查，不来自 Actor 自述（Verifier 一等接口）
- 每任务一提交；测试命令统一 `pytest`（根目录跑全量，包内可单跑）
- 模型访问全部经 ModelGateway；业务/示例代码不得直连供应商 SDK
- 包导入名 = 目录名：`agent_core` `agent_sdk` `agent_explore` `agent_evolve` `agent_adapters` `agent_devtools` `examples`

## Review Focus

1. **动作执行中取消/超时**：副作用已发出但回执未到 → `OUTCOME_UNKNOWN` 而非 FAILED；恢复不得重复触发副作用 → 测试钉在 T11
2. **波次内单任务崩溃**：一个 PracticeTask 抛异常不得杀死整波（ExceptionGroup 收敛为失败 outcome）→ 钉在 T20
3. **合并期记忆冲突**：同条件反结论的两条候选 → 双双 DISPUTED + 触发 contrast 练习，禁止覆盖 → 钉在 T14/T20
4. **技能子进程超时/失控**：kill + 清理 + 动作 FAILED，运行时不挂起 → 钉在 T16
5. **课程生成器输出畸形**（schema 不符）：走校验-重试-降级路径，不崩溃 → 钉在 T19

---

## Phase 0 — 奠基

### Task 1: uv monorepo 脚手架

**Files:**
- Create: `pyproject.toml`（workspace 根）、`agent-core/pyproject.toml` 等 7 个包的 pyproject、各包 `src/<pkg>/__init__.py`、根 `conftest.py`、`.gitignore`

**Interfaces:**
- Produces: 可安装的 workspace；包名 `agent-core`(dist) / `agent_core`(import) 等；根目录 `pytest` 可发现全部包测试

- [ ] **Step 1: 写根 pyproject（uv workspace）**

```toml
[tool.uv.workspace]
members = ["agent-core", "agent-sdk", "agent-explore", "agent-evolve", "agent-adapters", "agent-devtools", "examples"]
```
根 `[tool.pytest.ini_options]`：`asyncio_mode = "auto"`，`testpaths = ["agent-core/tests", ...]`（7 包）。dev 依赖：pytest、pytest-asyncio、ruff。每个子包 pyproject 声明 `requires-python = ">=3.11"` 与依赖（agent-core: pydantic>=2, aiosqlite；agent-explore/evolve: 依赖 agent-core；agent-adapters: openai, websockets, Pillow 归 examples…按需最小）。

- [ ] **Step 2: `uv sync` 验证可解析、`pytest` 空跑通过**

Run: `uv sync && uv run pytest`
Expected: 无错误、collected 0 items

- [ ] **Step 3: ruff 配置（line-length 100）+ 各包 `__init__.py` 空实现**

- [ ] **Step 4: Commit** `chore: uv workspace scaffold, 7 packages`

### Task 2: 供应商可用性探测脚本

**Files:**
- Create: `scripts/provider_probe.py`、`agent-adapters/tests/test_provider_probe.py`

**Interfaces:**
- Produces: `probe_providers(config: ProbeConfig) -> ProbeReport`；环境变量 `EWC_BASE_URL` `EWC_API_KEY` `EWC_MODEL_CHAT` `EWC_MODEL_VLM` `EWC_MODEL_REALTIME`；输出 `reports/provider_probe.json`

- [ ] **Step 1: 失败测试**：mock httpx/websockets 传输，断言 chat/VLM/realtime 三探测各自返回 `{available: bool, detail: str}`，汇总写 json
- [ ] **Step 2: 跑测试确认失败**（模块不存在）
- [ ] **Step 3: 实现**：chat/VLM 用一次真实小请求；realtime 只探测端点握手（不进音频）。真实请求仅在显式运行脚本时发生，单测全 mock
- [ ] **Step 4: `uv run pytest agent-adapters/tests/test_provider_probe.py -v` 通过**
- [ ] **Step 5: 运行 `uv run python scripts/provider_probe.py`，把结果 json 提交**（kickoff 核实，见 spec §9.1；若 realtime 不可用，T24 换供应商，不阻塞其他任务）
- [ ] **Step 6: Commit** `feat: provider availability probe`

---

## Phase 1 — agent-core 契约与状态机

### Task 3: RuntimeEvent 与标识基元

**Files:**
- Create: `agent-core/src/agent_core/events.py`
- Test: `agent-core/tests/test_events.py`

**Interfaces:**
- Produces:
  - `class RuntimeEvent(BaseModel)`: `id: str`（`ev_` 前缀 uuid4hex）、`type: str`、`ts: datetime`、`subject_type: Literal["task","run","action","agent","gap","memory","release"]`、`subject_id: str`、`causation_id: str | None`、`correlation_id: str | None`、`payload: dict`
  - `def new_event(type: str, subject_type: str, subject_id: str, **payload) -> RuntimeEvent`

- [ ] **Step 1: 失败测试**：`test_new_event_defaults`（id 前缀/ts 自动）、`test_event_is_pydantic_json_serializable`（`model_dump_json` 往返字段不变）
- [ ] **Step 2: 确认失败 → Step 3: 实现 → Step 4: `uv run pytest agent-core/tests/test_events.py -v` 通过 → Step 5: Commit** `feat: RuntimeEvent primitive`

### Task 4: Task/Run 状态机

**Files:**
- Create: `agent-core/src/agent_core/task.py`、`agent-core/src/agent_core/run.py`
- Test: `agent-core/tests/test_run_state.py`

**Interfaces:**
- Produces:
  - `class TaskSpec(BaseModel)`: `id, kind: str, goal: dict, input_schema: dict, output_schema: dict | None, constraints: dict, acceptance_criteria: list[dict]`
  - `class TaskHandle(BaseModel)`: `id, spec_id, parent_task_id: str | None, assigned_agent: str | None, created_at, updated_at`
  - `class RunState(str, Enum)`: `CREATED QUEUED RUNNING SUSPENDED SUCCEEDED FAILED CANCELLING CANCELLED`
  - `class WaitReason(str, Enum)`: `INPUT APPROVAL CHILD_TASK MESSAGE TIMER RESOURCE RECONCILIATION`
  - `class RunHandle(BaseModel)`: `id, task_id, agent_id, state: RunState, wait_reason: WaitReason | None, created_at, updated_at`
  - `def run_transition(run: RunHandle, target: RunState, wait_reason: WaitReason | None = None) -> RunHandle`——显式转移表 + 守卫，非法转移抛 `IllegalTransition(Exception)`；`SUSPENDED` 必须带 `wait_reason`；终态不可再转移；重新执行 = 新 Run 关联原 Run（`rerun_of` 字段）
  - 合法转移表（spec §5.3，锁定）：CREATED→QUEUED；QUEUED→RUNNING|CANCELLED；RUNNING→SUSPENDED|SUCCEEDED|FAILED|CANCELLING；SUSPENDED→RUNNING|CANCELLING；CANCELLING→CANCELLED

- [ ] **Step 1: 失败测试**：`test_happy_path`、`test_illegal_transition_raises`（如 QUEUED→SUCCEEDED）、`test_suspend_requires_reason`、`test_terminal_is_final`、`test_rerun_creates_new_run_linked`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: task/run state machines with explicit transition table`

### Task 5: Action 状态机与请求/结果

**Files:**
- Create: `agent-core/src/agent_core/action.py`
- Test: `agent-core/tests/test_action_state.py`

**Interfaces:**
- Produces:
  - `class ActionMode(str, Enum)`: `PREVIEW EXECUTE SIMULATE DELEGATE`
  - `class ActionRequest(BaseModel)`: `id, task_id, run_id, actor_id, capability_id, arguments: dict, mode: ActionMode, justification: dict`
  - `class ActionStatus(str, Enum)`: `PROPOSED VALIDATED REJECTED AUTHORIZED DISPATCHED SUCCEEDED FAILED OUTCOME_UNKNOWN`
  - `class ActionResult(BaseModel)`: `action_id, status: ActionStatus, output: dict, artifacts: list[str], evidence_refs: list[str], error: dict | None`
  - `def action_transition(status, target, *, timed_out: bool = False) -> ActionStatus`——DISPATCHED→OUTCOME_UNKNOWN 仅当 `timed_out`（回执丢失语义：不自动重试）；FAILED/SUCCEEDED/OUTCOME_UNKNOWN 为终态
- [ ] **Step 1: 失败测试**：`test_rejected_is_terminal`、`test_timeout_yields_outcome_unknown_not_failed`、`test_outcome_unknown_requires_timeout_flag`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: action request/result and dispatch state machine`

### Task 6: 能力目录与策略引擎

**Files:**
- Create: `agent-core/src/agent_core/capability.py`、`agent-core/src/agent_core/policy.py`
- Test: `agent-core/tests/test_capability.py`、`agent-core/tests/test_policy.py`

**Interfaces:**
- Produces:
  - `class CapabilityType(str, Enum)`: `TOOL SKILL MODEL RETRIEVER`
  - `class CapabilityDescriptor(BaseModel)`: `id, type: CapabilityType, name, input_schema: dict, output_schema: dict, side_effect_level: Literal["none","read","write","irreversible"], auth_scope: list[str], version: str`
  - `class CapabilityRegistry(Protocol)`: `register(desc) / discover(query: dict) -> list[CapabilityDescriptor] / resolve(capability_id) -> CapabilityDescriptor`；实现 `InMemoryCapabilityRegistry`（discover 按 type/tags 精确过滤）
  - `class Decision(BaseModel)`: `allowed: bool, reason: str, requires_approval: bool`
  - `class PolicyEngine(Protocol)`: `authorize(action: ActionRequest, context: dict) -> Decision`、`enforce_budget(run_id: str, usage: dict) -> Decision`；实现 `DefaultPolicyEngine`（规则：`side_effect_level == "irreversible"` ⇒ `requires_approval=True`；`auth_scope` 不满足 ⇒ 拒绝；预算字段超限 ⇒ 拒绝）
- [ ] **Step 1: 失败测试**：`test_irreversible_requires_approval`、`test_missing_auth_scope_denied`、`test_budget_exceeded_denied`、`test_registry_discover_by_type`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: capability registry and policy engine`

### Task 7: 证据对象与 EvidenceStore

**Files:**
- Create: `agent-core/src/agent_core/evidence.py`
- Test: `agent-core/tests/test_evidence.py`

**Interfaces:**
- Produces:
  - `class Asset(BaseModel)`: `id, kind: Literal["image","audio","video","document","structured","binary"], storage_ref: str, sha256: str, permissions: dict, source: str, version: int`
  - `class Segment(BaseModel)`: `id, asset_id, time_range: tuple[float, float] | None, spatial: dict | None, channel: str | None`
  - `class Observation(BaseModel)`: `id, ts, environment_version: str, asset_refs: list[str], state_summary: dict`
  - `class Evidence(BaseModel)`: `id, claim: dict, observation_refs: list[str], derivation: dict, verification_status: Literal["unverified","verified","disputed"]`
  - `class EvidenceStore(Protocol)`: `write_observation(obs) -> str`、`write_evidence(claim, observation_refs, derivation) -> str`、`trace_lineage(evidence_id) -> dict`；实现 `SqliteEvidenceStore(db_path, blob_dir)`——Asset 二进制按 `sha256` 内容寻址存 `blob_dir/<sha256>`（同哈希不重写，永不覆盖）
- [ ] **Step 1: 失败测试**：`test_blob_content_addressing_immutable`（写两次同内容只有一个 blob 文件）、`test_lineage_links_evidence_to_observations`、`test_derived_never_overwrites_original`
- [ ] **Step 2–5: 实现（aiosqlite）→ 通过 → Commit** `feat: evidence objects and sqlite/blob store`

### Task 8: ModelGateway 契约与 Fake 适配器

**Files:**
- Create: `agent-core/src/agent_core/models.py`
- Test: `agent-core/tests/test_model_gateway.py`

**Interfaces:**
- Produces:
  - `class Modality(str, Enum)`: `TEXT IMAGE AUDIO VIDEO STRUCTURED`
  - `class ModelCapabilities(BaseModel)`: `model_id: str, inputs: set[Modality], outputs: set[Modality], interaction: set[Literal["batch","streaming","realtime"]], tools: set[Literal["native_tool_call","structured_action"]], constraints: dict`
  - `class InferRequest(BaseModel)`: `model_id: str, messages: list[dict], tools: list[dict] | None, structured_output: dict | None, modalities: list[Modality]`；`class InferResponse(BaseModel)`: `text: str, tool_calls: list[dict], usage: dict, raw: dict | None`
  - `class ModelAdapter(Protocol)`: `def capabilities(self) -> ModelCapabilities`、`async def infer(self, request: InferRequest) -> InferResponse`
  - `class SessionConfig(BaseModel)`: `model_id, voice: str | None, instructions: str | None, tools: list[dict] | None`；`class SessionEvent(BaseModel)`: `type: Literal["opened","transcript_in","transcript_out","tool_call","audio_out","interrupted","error","closed"]`, payload: dict
  - `class SessionAdapter(Protocol)`: `def capabilities(self) -> ModelCapabilities`、`async def open_session(self, config: SessionConfig) -> str`、`async def stream(self, session_id: str)`（AsyncIterator[SessionEvent]）、`async def send_text/send_audio(session_id, ...)`、`async def interrupt(session_id)`、`async def close_session(session_id)`
  - `class ModelGateway`: `register(adapter)`、`route(requirements: dict) -> ModelAdapter`（按能力匹配，不匹配抛 `NoModelAvailable`）；V1 `route` 仅供 infer 路由，session 适配器按 id 显式获取 `session_adapter(model_id)`
  - `class FakeModelAdapter`：脚本化响应队列（测试用，进程内，零网络）
- [ ] **Step 1: 失败测试**：`test_route_matches_requirements`、`test_route_no_match_raises`、`test_fake_adapter_scripted_responses`、`test_capabilities_declared_independently`（audio in + audio out ≠ realtime——`route(interaction={"realtime"})` 不匹配 batch-only 声明）
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: model gateway contracts + fake adapter`

### Task 9: OpenAI 兼容 infer 适配器

**Files:**
- Create: `agent-adapters/src/agent_adapters/openai_compat.py`
- Test: `agent-adapters/tests/test_openai_compat.py`

**Interfaces:**
- Consumes: T8 的 `ModelAdapter/InferRequest/InferResponse/ModelCapabilities`
- Produces: `class OpenAICompatAdapter(ModelAdapter)`——构造参数 `(base_url, api_key, model_id, capabilities: ModelCapabilities)`；内部用 `openai` AsyncClient 指向 base_url；`infer` 映射 messages/tools/structured_output，usage 从响应取（缺省补 0）

- [ ] **Step 1: 失败测试**：mock `openai.AsyncClient`，断言请求映射（tools 透传、structured_output→response_format）与响应解析（tool_calls/usage）
- [ ] **Step 2–5: 实现 → 通过（单测全 mock，不打真实网络）→ Commit** `feat: openai-compatible infer adapter`

---

## Phase 2 — 运行时

### Task 10: EventLog 与 StateStore（检查点）

**Files:**
- Create: `agent-core/src/agent_core/runtime/eventlog.py`、`agent-core/src/agent_core/runtime/statestore.py`
- Test: `agent-core/tests/test_eventlog.py`、`agent-core/tests/test_statestore.py`

**Interfaces:**
- Produces:
  - `class EventLog(Protocol)`: `append(event: RuntimeEvent)`、`iter_events(correlation_id: str) -> AsyncIterator[RuntimeEvent]`；实现 `SqliteEventLog(db_path)`（append-only，无 update/delete）
  - `class StateStore(Protocol)`: `save_run/load_run(run_id)`、`save_task/load_task`、`save_checkpoint(run_id, state: dict)`、`load_latest_checkpoint(run_id) -> dict | None`；实现 `SqliteStateStore(db_path)`
- [ ] **Step 1: 失败测试**：`test_eventlog_append_only`（写后按 correlation_id 读回顺序一致）、`test_statestore_roundtrip`、`test_checkpoint_latest_wins`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: append-only event log + sqlite state store`

### Task 11: 执行驱动与 AgentRuntime

**Files:**
- Create: `agent-core/src/agent_core/runtime/driver.py`、`agent-core/src/agent_core/runtime/runtime.py`、`agent-core/src/agent_core/runtime/executor.py`
- Test: `agent-core/tests/test_runtime_loop.py`

**Interfaces:**
- Consumes: T4/T5/T6/T8/T10 全部契约
- Produces:
  - `class AgentSpec(BaseModel)`: `id, role, capabilities: list[str], execution_policy: Literal["model_loop"], model_id: str`
  - `class ExecutionPolicy(Protocol)`（spec §5.3 契约名）: `async def propose_next_actions(run: RunHandle, context: dict) -> list[ActionRequest]`、`def should_yield(run: RunHandle, context: dict) -> bool`；内置实现 `class ModelLoopPolicy`——从 context 取任务+记忆上下文，经 `ModelGateway.infer`（带注册能力 schema）产出 ActionRequest（mode=EXECUTE）
  - `class Verifier(Protocol)`（spec 一等契约）: `async def verify_action(request: ActionRequest, result: ActionResult) -> VerificationResult`、`async def verify_task(task: TaskHandle, evidence_refs: list[str]) -> VerificationResult`；`class VerificationResult(BaseModel): passed: bool, checks: list[dict], evidence_refs: list[str]`
  - `InMemoryCapabilityRegistry.bind(descriptor, handler: Callable)`——描述符与可调用实现的绑定（executor 依赖）
  - Create: `agent-core/src/agent_core/reserved.py`——仅定义 `ProtocolAdapter / CompositionPolicy / AgentTransport / EnvironmentAdapter` 空 Protocol（spec §5.3"仅定义不实现"预留，导入即可，无行为）
  - `class CapabilityExecutor`：`async def dispatch(action: ActionRequest) -> ActionResult`——TOOL：进程内调用注册的可调用能力；SKILL：交给 T16 的沙箱执行器（本任务先留 Protocol 注入点 `skill_runner`，默认拒绝执行）；SIMULATE：只返回干跑描述
  - `class AgentRuntime`：`async def submit(task: TaskSpec, agent: AgentSpec) -> RunHandle`（校验→QUEUED→调度）；内部主循环：`driver.propose → policy.authorize → executor.dispatch → Observation/Evidence 落库 → EventLog 追加 → checkpoint`，直到 `should_yield` 或预算/验证终止；`async def cancel(run_id)`（CANCELLING→CANCELLED，已在途 dispatch 的动作按 T5 走 OUTCOME_UNKNOWN）；`async def verify_and_finish(run_id, verifier)`（COMPLETED 判定走 Verifier 结果）
  - 主循环每轮 checkpoint；`async def resume(run_id)`：从 checkpoint 恢复，**已 DISPATCHED 且无终态的动作不重新 dispatch**（查 EventLog 判定），进 RECONCILIATION 等待
- [ ] **Step 1: 失败测试**（用 FakeModelAdapter + 假 TOOL 能力）：
  - `test_loop_solves_simple_task`：脚本化模型两轮产出动作，任务到 SUCCEEDED，EventLog 含完整因果链
  - `test_policy_denial_yields_rejected_action_not_crash`
  - `test_cancel_mid_dispatch_yields_outcome_unknown`（Review Focus #1 前半）
  - `test_resume_does_not_refire_dispatched_actions`（Review Focus #1 后半：人为丢弃回执后 resume，断言无二次 dispatch 事件）
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: model-loop driver, capability executor, agent runtime with safe resume`

---

## Phase 3 — 记忆子系统

### Task 12: MemoryRecord 与 MemoryStore

**Files:**
- Create: `agent-core/src/agent_core/memory/record.py`、`agent-core/src/agent_core/memory/store.py`
- Test: `agent-core/tests/test_memory_record.py`

**Interfaces:**
- Produces:
  - `class MemoryKind(str, Enum)`: `OBSERVATION HYPOTHESIS RULE SKILL_REF FAILURE_PATTERN PROFILE`
  - `class VerificationState(str, Enum)`: `CANDIDATE VERIFIED DISPUTED REJECTED`
  - `class LifecycleState(str, Enum)`: `ACTIVE SUPERSEDED EXPIRED TOMBSTONED`
  - `class MemoryRecord(BaseModel)`: `id, revision: int, schema_version: int, kind: MemoryKind, namespace: str, owner: str, access_policy: dict, content: dict, artifact_refs: list[str], source_refs: list[str], evidence_refs: list[str], epistemic_status: Literal["observed","hypothesized","verified"], applicability: dict, environment_version: str | None, valid_from: datetime, expires_at: datetime | None, supersedes: str | None, superseded_by: str | None, dependency_refs: list[str], verification: VerificationState, lifecycle: LifecycleState`
  - 双状态机守卫函数：`def verify_transition(record, target: VerificationState, *, evidence_ok: bool) -> MemoryRecord`（CANDIDATE→VERIFIED 必须传 `evidence_ok=True`，即 evidence_refs 非空且验证报告通过）；`def lifecycle_transition(record, target: LifecycleState) -> MemoryRecord`（SUPERSEDE 自动写 `superseded_by`）
  - `class MemoryStore(Protocol)`: `put(record)`、`get(id)`、`query(namespace: str, filter: dict) -> list[MemoryRecord]`、`tombstone(id)`；实现 `SqliteMemoryStore(db_path)`；命名空间常量：`NS_SKILLS="skills" NS_RULES="rules" NS_GAPS="gaps" NS_COVERAGE="coverage" NS_FAILURES="failures" NS_PROFILES="profiles" NS_RELEASES="releases"`
- [ ] **Step 1: 失败测试**：`test_verify_requires_evidence`、`test_supersede_links_both_records`、`test_query_by_namespace_and_applicability`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: memory record dual state machine + sqlite store`

### Task 13: 召回与上下文组装

**Files:**
- Create: `agent-core/src/agent_core/memory/retrieve.py`
- Test: `agent-core/tests/test_memory_retrieve.py`

**Interfaces:**
- Consumes: T12 `MemoryRecord/MemoryStore`
- Produces:
  - `class MemoryRetriever(Protocol)`: `async def retrieve(scope: dict, task_context: dict, limit: int) -> list[MemoryRecord]`；实现 `SimpleRetriever`——确定性打分：`verification=VERIFIED 且 lifecycle=ACTIVE` 强加权、`environment_version` 不匹配降权为 0、关键词/标签命中计分（V1 无向量检索）
  - `class ContextAssembler(Protocol)`: `def assemble(records: list[MemoryRecord], token_budget: int) -> str`；实现 `TokenBudgetAssembler`——按分数降序、超预算截断并标注 `[memory truncated]`
- [ ] **Step 1: 失败测试**：`test_stale_environment_version_excluded`、`test_candidate_never_outranks_verified`、`test_assembler_respects_budget`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: deterministic retriever + token-budget context assembler`

### Task 14: 写入/整理/发布

**Files:**
- Create: `agent-core/src/agent_core/memory/writer.py`
- Test: `agent-core/tests/test_memory_writer.py`

**Interfaces:**
- Consumes: T7 EvidenceStore、T12 MemoryStore
- Produces:
  - `class MemoryWriter(Protocol)`: `async def propose(kind, content, applicability, evidence_refs, source_refs) -> MemoryRecord`（一律 CANDIDATE/observed 或 hypothesized，禁止直接产生 VERIFIED）、`async def validate(record) -> ValidationReport(valid: bool, reasons: list[str])`（主张-证据对齐检查：claim 覆盖的属性 ⊆ 证据检查过的属性）、`async def commit(record) -> MemoryRecord`（入库）
  - `class MemoryConflict(BaseModel)`: `record_a, record_b, dimension: str`
  - `class MemoryConsolidator(Protocol)`: `async def detect_conflicts(records: list[MemoryRecord]) -> list[MemoryConflict]`——`applicability` 相同而 content 结论相反 ⇒ 冲突；`async def resolve(conflict) -> ConflictResolution`：条件不同拆两条、条件相同 ⇒ 双双置 DISPUTED + 返回 `contrast_task_hint: dict`（喂给课程层，禁止覆盖）
  - `class MemoryPublisher(Protocol)`: `async def publish(record_id, verifier_report: dict) -> MemoryRecord`（CANDIDATE→VERIFIED，写 verification 报告引用）、`async def invalidate(record_id, reason)`（降级/失效并沿 `dependency_refs` 传播标记）
- [ ] **Step 1: 失败测试**：
  - `test_propose_never_verified`、`test_validate_rejects_unsupported_claims`（"文件可打开"证据不能支持"参数最优"主张）
  - `test_conflict_same_conditions_marks_both_disputed`（Review Focus #3 前半）
  - `test_invalidate_propagates_to_dependents`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: memory writer/consolidator/publisher with claim-level gates`

---

## Phase 4 — 演化轨道（agent-evolve）

### Task 15: 候选评测与发布

**Files:**
- Create: `agent-evolve/src/agent_evolve/controller.py`
- Test: `agent-evolve/tests/test_evolution.py`

**Interfaces:**
- Consumes: T14 MemoryPublisher、T10 EventLog
- Produces:
  - `class CandidateType(str, Enum)`: `MEMORY SKILL PROMPT_POLICY`
  - `class ExperienceCandidate(BaseModel)`: `id, candidate_type: CandidateType, payload: dict, source_practice_ids: list[str], parent_release_id: str | None`
  - `class EvalReport(BaseModel)`: `mini_batch_scores: list[float], parent_scores: list[float] | None, improved: bool, regression_violations: list[str]`
  - `class ReleaseManifest(BaseModel)`: `release_id, created_at, models: dict, capabilities: dict, memory_snapshot_id: str, candidates: list[str]`
  - `class EvolutionController`: `submit(candidate) -> str`、`async evaluate(candidate_id, mini_batch: list[TaskSpec], runner) -> EvalReport`（父子同批次对比；`regression_violations` = 候选发布后旧场景回退项）、`async publish(candidate_id, report) -> ReleaseManifest`（`improved and not regression_violations` 才放行，否则拒绝并记事件）、`async rollback(release_id) -> ReleaseManifest`（生成反向 manifest，memory 走 invalidate 链）
- [ ] **Step 1: 失败测试**：`test_publish_requires_improvement_no_regression`、`test_rollback_generates_inverse_manifest`、`test_eval_compares_parent_on_same_batch`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: evolution controller with gated publish and rollback`

### Task 16: 技能沙箱执行器

**Files:**
- Create: `agent-evolve/src/agent_evolve/skill_sandbox.py`
- Test: `agent-evolve/tests/test_skill_sandbox.py`

**Interfaces:**
- Consumes: T11 `CapabilityExecutor.skill_runner` 注入点
- Produces:
  - `class SkillArtifact(BaseModel)`: `skill_id, version, description, preconditions: dict, entrypoint: str`（脚本相对路径）、`expected_outputs: dict, known_failure_scenarios: list[dict]`, `script_files: dict[str, str]`（文件名→内容）
  - `class SkillSandbox`: `async def run(artifact: SkillArtifact, args: dict, *, timeout_s: float = 30) -> SkillRunResult(status, stdout, stderr, artifacts, duration)`——`asyncio.create_subprocess_exec` 用系统 python 执行，`asyncio.wait_for` 超时 kill 进程组；stdout 上限 1MB 截断；脚本落盘到 `skill_workspace/<skill_id>/<version>/`
  - `async def contract_test(artifact) -> ContractTestReport`：跑 `expected_outputs` 与 `known_failure_scenarios` 中的样例，全过才算可发布（T15 的 SKILL 候选评测调用它）
- [ ] **Step 1: 失败测试**：
  - `test_timeout_kills_and_reports_failed`（Review Focus #4：sleep 死循环脚本 + timeout_s=0.5，断言进程被回收、status=FAILED、运行时不挂）
  - `test_stdout_truncated`、`test_contract_test_gates_publication`（失败场景样例不过 → report.passed=False）
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: subprocess skill sandbox with contract-test gate`

---

## Phase 5 — 探索闭环（agent-explore）

### Task 17: GapLedger 与练习对象模型

**Files:**
- Create: `agent-explore/src/agent_explore/gaps.py`、`agent-explore/src/agent_explore/practice.py`
- Test: `agent-explore/tests/test_gaps.py`、`agent-explore/tests/test_practice_models.py`

**Interfaces:**
- Produces:
  - `class GapType(str, Enum)`: `KNOWLEDGE SKILL TOOL PERCEPTION AUTHORIZATION VERIFICATION ENVIRONMENT`
  - `class GapState(str, Enum)`: `DISCOVERED TRIAGED ACTIONABLE WAITING_EXTERNAL PATCH_CANDIDATE_READY UNDER_EVALUATION RESOLVED WONT_FIX`
  - `class Gap(BaseModel)`: `id, gap_type, title, description, severity: float, recurrence: int, status: GapState, reproduce_conditions: dict, evidence_refs: list[str], closure_condition: str, affected_capabilities: list[str]`
  - `class GapLedger(Protocol)`: `record(gap) -> str / update(gap) / open_gaps(filter) -> list[Gap] / close(id, resolution)`；实现 `SqliteGapLedger`（AUTHORIZATION 类缺口状态固定 WAITING_EXTERNAL，不自动绕过）
  - `class TaskFamily(str, Enum)`: `PREREQUISITE VARIANT CONTRAST STRESS TARGET_ATTEMPT REGRESSION`
  - `class AcceptanceSpec(BaseModel)`: `verifier_profile: str, required_evidence: list[str], pass_conditions: list[dict], fail_conditions: list[dict]`
  - `class PracticeTask(BaseModel)`: `id, family: TaskFamily, title, objective: dict, setup: dict, constraints: dict, acceptance: AcceptanceSpec, priority: float, difficulty: float, depends_on: list[str], tags: list[str]`
  - `class PracticeWave(BaseModel)`: `wave_id, shared_memory_snapshot_id, task_ids: list[str], parallelism: int, merge_policy: Literal["sequential"]`
  - `class DiagnosisType(str, Enum)`: `MISSING_PREREQUISITE WRONG_CAUSAL_MODEL OVERGENERALIZED_RULE VERIFICATION_GAP FRAGILE_SUCCESS`
- [ ] **Step 1: 失败测试**：`test_auth_gap_never_auto_actionable`、`test_practice_task_requires_acceptance`（无 acceptance 构造即抛 ValidationError）
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: gap ledger and practice object models`

### Task 18: 课程记忆视图与组装器

**Files:**
- Create: `agent-explore/src/agent_explore/curriculum_memory.py`
- Test: `agent-explore/tests/test_curriculum_memory.py`

**Interfaces:**
- Consumes: T12/T13 MemoryStore/Retriever、T17 GapLedger
- Produces（round-9 MVP 子集，spec §5.4）：
  - `class CapabilityStatus(BaseModel)`: `capability_id, label, confidence: float, evidence_count: int, last_seen_at: datetime | None`
  - `class CapabilityFrontier(BaseModel)`: `mastered: list[CapabilityStatus], shaky: list[CapabilityStatus], missing: list[str], blocked: list[str]`
  - `class CurriculumMemoryView(BaseModel)`: `mode: str, session_state: dict, frontier: CapabilityFrontier, gaps: list[Gap], rules: list[MemoryRecord], skills: list[MemoryRecord], failures: list[MemoryRecord], signals: list[dict]`
  - `class CurriculumMemoryAssembler(Protocol)`: `async def assemble(mode: str, memory_store, gap_ledger, recent_outcomes: list[dict]) -> CurriculumMemoryView`
  - 默认实现 `DefaultAssembler`：frontier 由 `skills/*`+`failures/*` 命名空间统计（成功率≥0.8 且 evidence≥3 → mastered；0.3–0.8 → shaky；GapType.SKILL 未关闭 → missing；AUTHORIZATION → blocked）；signals 输出压缩提示码（如 `FOCUS_SHAKY:<cap>`、`EXPAND_MISSING:<cap>`）
- [ ] **Step 1: 失败测试**：`test_frontier_classification_thresholds`、`test_candidate_and_stale_excluded_from_view`、`test_signals_summarize_frontier`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: curriculum memory view assembler`

### Task 19: 课程生成器与探索策略

**Files:**
- Create: `agent-explore/src/agent_explore/curriculum.py`
- Test: `agent-explore/tests/test_curriculum_generator.py`

**Interfaces:**
- Consumes: T8 ModelGateway（LLM 出题）、T18 CurriculumMemoryView、T17 模型
- Produces:
  - `class CurriculumMode(str, Enum)`: `BROAD DEEP MIXED`
  - `class CurriculumDecision(BaseModel)`: `action: Literal["continue","switch_mode","stop","escalate"], reason: str, suggested_next_mode: CurriculumMode | None`
  - `class CurriculumGenerator(Protocol)`: `async def plan_wave(view, mode, budget) -> PracticeWave`、`async def refine_after_result(prev_task, outcome, diagnosis, view) -> PracticeTask | CurriculumDecision`、`async def should_stop(view, budget_status) -> CurriculumDecision`
  - `class LLMCurriculumGenerator`：经 `ModelGateway.infer`（structured_output 生成 PracticeWave/PracticeTask/Decision 的 JSON），输出先过 Pydantic 校验；**畸形输出 → 一次重试（附带校验错误信息）→ 再失败抛 `CurriculumGenerationError`**（Review Focus #5）
  - `class ExplorationBudget(BaseModel)`: `max_tokens: int | None, max_actions: int | None, wall_clock_s: int | None, max_cost: float | None`；`class BudgetMeter`：`consume(**delta) -> BudgetStatus(remaining: dict, exhausted: bool)`
  - 诊断→任务族映射常量 `DIAGNOSIS_FAMILY_MAP`：MISSING_PREREQUISITE→PREREQUISITE；WRONG_CAUSAL_MODEL→CONTRAST；OVERGENERALIZED_RULE→CONTRAST；FRAGILE_SUCCESS→STRESS；VERIFICATION_GAP→VARIANT
- [ ] **Step 1: 失败测试**：
  - `test_malformed_plan_retried_once_then_raises`（Review Focus #5：FakeModelAdapter 第一次返回非法 JSON、第二次合法 → 成功；两次都非法 → CurriculumGenerationError）
  - `test_diagnosis_maps_to_family`、`test_stop_on_budget_exhausted`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: LLM curriculum generator with validation-retry, budget meter`

### Task 20: BRS/DRS 探索控制器

**Files:**
- Create: `agent-explore/src/agent_explore/controller.py`
- Test: `agent-explore/tests/test_exploration_controller.py`

**Interfaces:**
- Consumes: T11 AgentRuntime、T14 Writer/Consolidator、T15 EvolutionController、T17–T19 全部
- Produces:
  - `class ExplorationPolicy(BaseModel)`（spec §5.3 契约名）: `mode: CurriculumMode, budget: ExplorationBudget, max_waves: int | None, strategies: list[str] = ["breadth","gap_driven","counterexample"]`
  - `class ExplorationReport(BaseModel)`: `waves_executed: int, tasks_executed: int, tasks_passed: int, candidates_generated: int, candidates_published: int, gaps_opened: int, budget_spent: dict`
  - `class ExplorationController`: `async def explore(target: TaskSpec, policy: ExplorationPolicy, verifier: Verifier, app_context) -> ExplorationReport`
  - **BRS 循环**（`explore` 入口按 `policy` 驱动）：`should_stop? → plan_wave（共享记忆快照 id）→ asyncio.TaskGroup 并行执行 PracticeTask（每个任务 = 提交 AgentRuntime + 独立 Verifier 验收 → PracticeOutcome）→ 整波完成按序合并候选（Writer.propose→validate→Consolidator 冲突检查→冲突项 DISPUTED 并把 contrast_task_hint 追加到下一轮课程输入）→ BudgetMeter 检查`
  - **DRS 循环**：`refine_after_result(diagnosis=从失败/脆弱成功归纳) → 单任务执行 → 验证 → 先整合记忆再出下一题 → should_stop`
  - 波内单任务异常捕获在 TaskGroup 回调收进 outcome（status=ERROR），不取消姊妹任务（Review Focus #2）
- [ ] **Step 1: 失败测试**（全 Fake 模型 + 假环境）：
  - `test_brs_wave_runs_parallel_and_merges_sequentially`（断言姊妹任务从同一 memory_snapshot_id 出发）
  - `test_single_task_crash_does_not_kill_wave`（Review Focus #2：一个任务抛 RuntimeError，波仍产出其余 outcome）
  - `test_conflict_becomes_contrast_task_next_wave`（Review Focus #3 后半）
  - `test_drs_integrates_memory_before_next_task`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: BRS/DRS exploration controller`

---

## Phase 6 — 工作台与主实验

### Task 21: 合成文档工作台（生成器+工具+验收）

**Files:**
- Create: `examples/src/examples/docworkbench/generator.py`、`.../tools.py`、`.../acceptance.py`、`.../corpus.py`
- Test: `examples/tests/test_workbench.py`

**Interfaces:**
- Produces:
  - `generate_corpus(seed: int, size: int, difficulty: float) -> Corpus`——`Corpus(documents: list[Asset+渲染PNG], tasks: list[PracticeTask|TaskSpec], acceptance_scripts: dict[task_id, Callable], ground_truth: dict[task_id, dict])`；Pillow 渲染混合版式（表格报表/表单/带噪声扫描风/图表截图），字段密度与干扰项随 difficulty
  - 文档工具（注册为 CapabilityDescriptor，side_effect_level 如实声明）：`parse_document / extract_fields / convert_format / export_document / validate_form`——**埋入可发现约束**（例：`export_document(format="xlsx")` 对含图表扫描页的文档静默丢页，除非先 `convert_format(dpi=200)`；`validate_form` 对某类字段存在隐藏例外分支；`extract_fields` 对双栏版式需显式 region 参数）——约束集中在 `tools.py` 的 `HIDDEN_CONSTRAINTS` 常量表（真值可审计）
  - `run_acceptance(task_id, result, corpus) -> AcceptanceReport(passed: bool, checks: list[dict])`——纯 Python 规则检查，全自动
  - `split_corpus(corpus, practice_ratio: float) -> tuple[practice, heldout]`——按能力维度分层切分（每维标签均衡），heldout 探索期不可见
- [ ] **Step 1: 失败测试**：`test_corpus_deterministic_by_seed`、`test_hidden_constraints_actually_bite`（不知约束的朴素调用断言失败/丢页）、`test_split_balances_capability_dims`、`test_acceptance_is_pure_python`
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: synthetic document workbench with hidden constraints`

### Task 22: 文档 Agent 应用与端到端冒烟

**Files:**
- Create: `examples/src/examples/docworkbench/app.py`
- Test: `examples/tests/test_doc_app_e2e.py`

**Interfaces:**
- Consumes: T11 AgentRuntime、T20 ExplorationController、T21 工作台
- Produces: `def build_app(model_adapter, db_path, blob_dir, corpus) -> DocumentAgentApp`——组装 registry（文档工具）/gateway/policy/evidence/memory/explore，提供 `async app.solve(task) -> RunHandle` 与 `async app.explore(budget) -> ExplorationReport`（explore 写入的记忆经 T15 发布后对 solve 生效）
- [ ] **Step 1: 失败测试（Fake 模型，脚本化"先犯错后用记忆纠正"）**：`test_explore_then_solve_beats_cold_solve`——冷启动 solve 一道带隐藏约束的题失败；explore（Fake 课程产出一道撞出该约束的练习）后 solve 同类题通过
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: document agent app, e2e explore-then-solve smoke`

### Task 23: 同预算对照实验与记忆审计

**Files:**
- Create: `examples/src/examples/docworkbench/experiment.py`、`.../audit.py`
- Test: `examples/tests/test_experiment.py`

**Interfaces:**
- Consumes: T21/T22、T19 BudgetMeter
- Produces:
  - `class ExperimentConfig(BaseModel)`: `seeds: list[int]`, `budget_w: ExplorationBudget`, `practice_ratio: float`, `model: dict`
  - `async def run_experiment(config) -> ExperimentReport`——每种子两臂：**explore 臂** = budget_w 全投探索后评 heldout；**retry 臂** = 等预算直接反复 target_attempt（不写长期记忆）后评 heldout；报告含每臂均值±方差（整体+按能力分组）
  - `async def audit_promoted(app, corpus) -> AuditReport`——晋级记忆逐条对照 `HIDDEN_CONSTRAINTS` 真值表 + 强模型复核（经 gateway），产出 `error_promotion_rate / counterexample_found / regression_rate`
- [ ] **Step 1: 失败测试**（Fake 模型）：`test_two_arms_equal_budget`（BudgetMeter 断言两臂消耗同量）、`test_report_has_mean_and_variance_per_group`、`test_audit_detects_wrong_memory`（植入一条错误规则，audit 必须抓住）
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: same-budget experiment harness + memory audit`

---

## Phase 7 — 第二切片：实时语音（前置：T2 探测确认 Realtime 可用）

### Task 24: Realtime 会话适配器与 Omni 示例

**Files:**
- Create: `agent-adapters/src/agent_adapters/realtime.py`、`examples/src/examples/omni/app.py`
- Test: `agent-adapters/tests/test_realtime.py`、`examples/tests/test_omni_e2e.py`

**Interfaces:**
- Consumes: T8 SessionAdapter/SessionEvent、T16 技能、T13 ContextAssembler
- Produces:
  - `class RealtimeSessionAdapter(SessionAdapter)`：WebSocket 连供应商 Realtime 端点；`stream()` 产出 SessionEvent 流（含 `interrupted`）；背压 = 有界 asyncio.Queue（满则丢弃最旧 audio_out 分片并记事件）；`interrupt()` 发 barge-in 指令；断线自动重连一次并重放 session 状态（`test_realtime.py` 起本地 fake ws server 全确定性测试）
  - `examples/omni/app.py`：`async def run_omni_session(app, seconds: float)`——实时 Agent 消费已发布技能/记忆（ContextAssembler 注入 instructions），会话中 tool_call 走 CapabilityExecutor
- [ ] **Step 1: 失败测试**：
  - `test_interrupt_event_propagates`、`test_reconnect_resumes_without_losing_session_state`（Review Focus 延伸：断线重连后 transcript 连续、技能引用完整）
  - `test_omni_tool_call_uses_published_skill`（e2e，fake ws）
- [ ] **Step 2–5: 实现 → 通过 → Commit** `feat: realtime session adapter + omni voice example`

---

## Phase 8 — 验收与工具

### Task 25: 扩展性验收套件与最小 CLI

**Files:**
- Create: `tests_acceptance/test_extensibility.py`、`agent-devtools/src/agent_devtools/cli.py`
- Test: 自身即验收测试（spec §7.3/§7.4）

**Interfaces:**
- Consumes: 全部前序
- Produces:
  - 四项不改 Core 扩展测试（全 Fake 模型）：`test_swap_model_adapter`（换 FakeModelAdapter 实例应用照跑）、`test_add_new_tool_capability`、`test_custom_verifier`（注入领域 Verifier 生效）、`test_run_with_exploration_disabled`（纯 execute 模式零探索依赖可跑）
  - CLI：`python -m agent_devtools run-experiment --config <json>`、`show-run <run_id>`（EventLog 时间线）、`show-memory [--ns skills|rules|...]`
- [ ] **Step 1: 写四项验收测试并确认全绿（这是验收本身，先绿后补 CLI）**
- [ ] **Step 2: 实现 CLI（click），`--help` 三命令可用**
- [ ] **Step 3: `uv run pytest` 全仓通过 + ruff 无告警**
- [ ] **Step 4: Commit** `feat: extensibility acceptance suite + devtools CLI`

---

## 执行顺序与依赖

T1→T2→(T3→T4→T5→T6→T7→T8→T9)→(T10→T11)→(T12→T13→T14)→(T15→T16)→(T17→T18→T19→T20)→(T21→T22→T23)→T24→T25。Phase 1 内 T3–T8 除 T8 依赖 T3 外基本独立可并行；T24 仅依赖 T2 结论 + T8/T13/T16，可与 Phase 5/6 并行。

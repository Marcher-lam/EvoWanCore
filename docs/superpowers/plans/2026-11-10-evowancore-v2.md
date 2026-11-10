# EvoWanCore V2 实现计划（全深版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让系统学会它没学会的东西（extract 0.33→≥0.67，错误晋级率保持 0）+ VLM 图文管道 + MCP 双向接入 + 地基债清偿。

**Architecture:** B 块把发布门禁从环境真值表切换到通用评测门（EvolutionController 父子对比+回归）；C-VLM 把字段值移入渲染 PNG、以低对比度感知陷阱为第四隐藏约束接入既有探索闭环；C-MCP 用官方 python SDK 实装 ProtocolAdapter 双向；A 块按 V1 评审 M 清单原文实施。

**Tech Stack:** 继承 V1（Python 3.11+/asyncio/Pydantic v2/SQLite/TDD）；新增 `mcp` SDK（仅 agent-adapters）；无真 VLM key——`WorkbenchFakeVLM` 按对比度模拟读图（T24 模式）。

**Spec:** `docs/superpowers/specs/2026-11-10-evowancore-v2-design.md`（含 §6 builder check 补答与 §9 M 清单；执行者须同时读）

## Global Constraints

- 继承 V1 全部：Python>=3.11、Pydantic v2 契约、Protocol SPI、每任务 TDD（先测后码）、每任务提交、ruff line-length 100
- **保留集任务绝对禁止进入任何评测门**（mini-batch/回归场景只允许练习池或新生成种子任务）
- 预算口径 = **真实模型调用计数**（探索臂的 reflector/课程/评测门全部计入；替换 tokens=len(tasks) 占位）
- 感知任务判分必须程序化对照渲染真值，不依赖模型自述
- MCP server 只暴露 `side_effect_level != "irreversible"` 且 `auth_scope == []` 的能力，每次调用过 PolicyEngine
- 沙箱网络隔离：平台不支持时显式报错，不静默跳过
- `mcp` 依赖只进 `agent-adapters`

## Review Focus

1. **评测门吃到保留集任务** → gate 校验拒绝（T12 钉测试）
2. **CANDIDATE 泄漏进执行上下文**（发布前影响 solve）→ 默认排除（T13 钉）
3. **感知陷阱不可学**（FakeVLM 恒对/恒错）→ 对比度条件化行为（T19 钉）
4. **MCP server 暴露不可逆能力** → 拒绝（T24 钉）
5. **两臂预算口径不一致** → 计数网关统一计量（T6 钉 + T16 实验断言 comparable）

---

## Phase S1 — 地基（A 块，9 任务）

### Task 1: M1 环境版本参数化出 Core
**Files:** Modify `agent-core/src/agent_core/runtime/runtime.py`、`agent-core/src/agent_core/memory/writer.py`；Test `agent-core/tests/test_env_version.py`
**Interfaces:** Produces: `AgentRuntime(..., environment_version: str = "unspecified")`；`DefaultMemoryWriter.propose(..., environment_version: str | None = None)` 盖戳到记录；runtime 检索 context 用构造值（删除 "workbench-1" 硬编码）；app 传 `ENV_VERSION`。
- [ ] RED：`test_runtime_env_version_injected`（构造 environment_version="wb-2" → submit 后 retriever context keywords 含该值；`test_writer_stamps_env_version`（propose 带 env → 记录 environment_version 命中）
- [ ] GREEN 实现 → `uv run pytest` 全绿 + ruff → Commit `fix(M1): environment version parameterized out of core`

### Task 2: M2/M3 两个潜伏 bug
**Files:** Modify `capability.py`（discover `is not`→`!=`）、`evidence.py`（trace_lineage 空 refs 返回空 lineage）
**Interfaces:** 无新面。
- [ ] RED：`test_discover_accepts_plain_string`（discover({"type":"tool"}) 命中）；`test_trace_lineage_empty_refs`（返回 {"evidence":…, "observations": []} 不抛 SQL 错）
- [ ] GREEN → gates → Commit `fix(M2,M3): discover string compare + empty lineage`

### Task 3: M7 cancel/verify 竞态
**Files:** Modify `runtime.py verify_and_finish`；Test `agent-core/tests/test_runtime_loop.py` 追加
**Interfaces:** verify_and_finish 终态迁移前**重读 store 最新 run**，若已 CANCELLING/CANCELLED 则只走取消路径。
- [ ] RED：`test_cancel_during_verify_yields_cancelled`（verifier 内 sleep，期间 cancel，最终 CANCELLED 而非 SUCCEEDED）
- [ ] GREEN → gates → Commit `fix(M7): cancel wins over stale verify transition`

### Task 4: M9 波次并发强制
**Files:** Modify `agent-explore/src/agent_explore/controller.py _run_wave`；Test 追加 `test_wave_parallelism_enforced`
**Interfaces:** PracticeWave.parallelism 生效：`asyncio.Semaphore(parallelism)` 包任务协程；测试用并行峰值计数器断言峰值 ≤ parallelism 且波仍完成。
- [ ] RED → GREEN → gates → Commit `fix(M9a): wave parallelism enforced`

### Task 5: 预算真实计量（Q3 口径）
**Files:** Create `agent-core/src/agent_core/models.py` 增 `CountingAdapter`；Modify `runtime.py`（构造收 `budget_meter`，每 action 后 consume+超限中断）、`controller.py`（占位 tokens=len(tasks) 删除，改由 CountingAdapter 计量）；Test `agent-core/tests/test_counting.py`
**Interfaces:** Produces: `CountingAdapter(inner: ModelAdapter)`——infer 后 `consume(usage)`；`self.calls: int`、`self.total_tokens: int`。AgentRuntime(budget_meter=None)；超限 → 循环 break → verify_and_finish 前置检查 meter.exhausted → FAILED(reason="budget")。
- [ ] RED：`test_counting_adapter_counts`；`test_runtime_budget_exhaustion_fails_run`（2 动作预算、3 提议 → FAILED 且事件含 budget）
- [ ] GREEN → gates → Commit `feat: real call-count budget accounting + runtime gate`

### Task 6: resume 续跑剩余步骤
**Files:** Modify `runtime.py`（抽 `_execution_loop(run, agent, context)`；checkpoint 存 context 子集）；Test 追加
**Interfaces:** resume：对账 pending → OUTCOME_UNKNOWN（保留）→ **重建 context 继续主循环** → verify_and_finish。
- [ ] RED：`test_resume_continues_remaining_steps`（两动作任务，第一动作完成后崩溃，resume 后第二动作执行且只派发一次）
- [ ] GREEN → gates → Commit `feat: resume continues execution loop`

### Task 7: M8 公有 API 替代私有访问
**Files:** Modify `runtime.py`（`async def events(run_id)` 公有）、`capability.py`（`rebind(descriptor, handler)` 覆盖绑定）、`examples/.../app.py`（convert 覆盖改 rebind；`build_app(..., verifier=None)` 可注入）；Test `tests_acceptance/test_extensibility.py` 更新（不再戳 `_verifier`）
**Interfaces:** Produces: `AgentRuntime.events(run_id) -> AsyncIterator[RuntimeEvent]`；`InMemoryCapabilityRegistry.rebind(...)`；`build_app(model_adapter, db_path, blob_dir, corpus, practice_part=None, verifier=None)`；controller `_run_practice` 改用 `runtime.events()`。
- [ ] RED：`test_public_events_api`；验收测试改用 `build_app(verifier=StrictVerifier())` → GREEN → gates → Commit `fix(M8): public APIs replace private reaches`

### Task 8: M10 演化态持久化 + 空契约 fail
**Files:** Modify `agent-evolve/src/agent_evolve/controller.py`（构造收 `db_path`，candidates/manifests 落 SQLite；publish 写 NS_RELEASES 记录）、`skill_sandbox.py`（contract_test：expected_outputs 与 failure_scenarios 皆空 → passed=False）
**Interfaces:** Produces: `EvolutionController(..., db_path: Path | None = None)`；拒绝发布时写 `PublishDenied` 事件进 EventLog（可选注入）。
- [ ] RED：`test_evolution_state_survives_restart`（新实例可 rollback 旧 manifest）；`test_empty_contract_fails`
- [ ] GREEN → gates → Commit `fix(M10): persisted evolution state + strict contract gate`

### Task 9: 沙箱网络隔离（A#1）
**Files:** Modify `skill_sandbox.py`；Test 追加
**Interfaces:** `SkillSandbox(..., network: Literal["deny","allow"] = "deny")`；deny 且平台支持 → 包装器（macOS `sandbox-exec` profile / Linux `unshare -n`）；不支持 → 显式 `ValueError`。默认 deny。
- [ ] RED：`test_network_deny_blocks_socket`（脚本连 127.0.0.1 失败即 pass，unix 实测）；`test_network_allow_permits`；`test_unsupported_platform_raises`
- [ ] GREEN → gates → Commit `feat: sandbox network isolation`

---

## Phase S2 — 闭环智能化（B 块，7 任务）

### Task 10: LLMReflector（结构化声明）
**Files:** Create `agent-explore/src/agent_explore/reflector.py`；Test `agent-explore/tests/test_reflector.py`
**Interfaces:** Produces: `LLMReflector(gateway, model_id)` 可调用 `(task, outcome) -> list[dict]`（controller 兼容 awaitable）；每条 proposal：`{"kind":"rule","content":{"statement", "claim":{"dimension","action","params"}}, "applicability", "evidence_refs"(仅取 outcome.evidence_refs)}`；LLM 输出走 PracticeWave 同款校验-重试（复用 `_infer_validated` 模式，提取为共享助手或本地实现）；失败/空 → `[]`。
- [ ] RED：`test_reflector_structured_output`（Fake 返回合法 JSON → 提案结构正确、evidence 白名单）；`test_reflector_malformed_returns_empty`
- [ ] GREEN → gates → Commit `feat: LLM reflector with structured claims`

### Task 11: 检索默认排除 CANDIDATE（关泄漏）
**Files:** Modify `agent-core/src/agent_core/memory/retrieve.py`；Test `test_memory_retrieve.py` 更新
**Interfaces:** `SimpleRetriever(store, include_candidates: bool = False)`；默认排除 CANDIDATE（V1 语义收紧，更新 `test_candidate_never_outranks_verified` 为 `test_candidate_excluded_by_default` + include_candidates=True 路径）。
- [ ] RED（改写旧测试为新语义）→ GREEN → gates → Commit `feat: candidates excluded from retrieval by default`

### Task 12: 评测门接入 app 发布路径
**Files:** Modify `examples/.../app.py`；Test `examples/tests/test_doc_app_e2e.py` 追加
**Interfaces:** Produces: `DocumentAgentApp.set_memory_visibility(include_candidates: bool)`（切 retriever 标志）；`_publish_candidates` 重写：对每条候选 `EvolutionController.submit/evaluate/publish`——DualRunner=同 app solve（with_candidate ↔ visibility），mini-batch 取练习池 ≤3、回归 ≤2；**gate 任务来源守卫**：`_gate_tasks(practice_ids, heldout_ids)` 拒绝保留集 id；真值门退为审计（不再 gate）。app 构造 EvolutionController(db_path=…)。
- [ ] RED：`test_gate_rejects_wrong_rule_via_eval`（reflector 出错规则 → 评测门拒绝 → 保持 CANDIDATE）；`test_gate_never_uses_heldout`（守卫抛错）；`test_good_rule_publishes_via_gate`
- [ ] GREEN（既有 e2e 需适配真规则过门）→ gates → Commit `feat: generic eval gate wired into publish path`

### Task 13: gap→contrast 课程闭环
**Files:** Modify `agent-explore/src/agent_explore/curriculum.py`（plan_wave 提示含 open gaps + 覆盖校验）；Test 追加
**Interfaces:** plan_wave 后置校验：`view.gaps` 非空时，波内任务 id 前缀（维度）必须覆盖至少一个 gap 的 affected 前缀；不满足 → 作为校验错误走重试（第二次仍不满足 → CurriculumGenerationError）。
- [ ] RED：`test_gap_drives_wave_coverage`（第一次计划不含 gap 维度 → 重试含 → 通过；两次都不含 → 抛）
- [ ] GREEN → gates → Commit `feat: gap-driven contrast curriculum`

### Task 14: 维度感知诊断
**Files:** Modify `controller.py _diagnose`；Test 追加
**Interfaces:** failed 且 task.id 前缀 ∈ {"extract","export"} → `wrong_causal_model`（→contrast 族）；其余 failed → missing_prerequisite。
- [ ] RED：`test_extract_failure_diagnoses_contrast` → GREEN → gates → Commit `feat: dimension-aware diagnosis`

### Task 15: 审计升级（结构化声明 + reviewer）
**Files:** Modify `examples/.../audit.py`、`tools.py HIDDEN_CONSTRAINTS` 增 claim 匹配键；Test 追加
**Interfaces:** `statement_matches_truth` 兼容结构化：content["claim"]["action"] 与 params 对照约束表（如 action=convert_format & params.dpi>=200 命中 export_needs_convert）；`audit_promoted(..., reviewer=None)` reviewer 接 gateway（app 提供 `make_gateway_reviewer()`）。
- [ ] RED：`test_audit_matches_structured_claim`（action 对 params 错 → 错误）；`test_audit_reviewer_overrides`
- [ ] GREEN → gates → Commit `feat: structured-claim audit + reviewer hook`

### Task 16: B 检查点实验（文本语料，真模型）
**Files:** Create `reports/exp_b_checkpoint.json`（gitignored 产物）→ 归档 `docs/experiments/round3_b_checkpoint.md`
**Interfaces:** Consumes: T5/T10/T12/T13。步骤：source .env → CLI run-experiment（难度 0.8、种子 31-33、max_actions 4）→ 断言：comparable=True、错误晋级率=0、explore ≥ retry（不达标 → 诊断迭代，不结任务）→ 归档提交。
- [ ] Run + 断言 → Commit `docs: B checkpoint experiment (text corpus)`

---

## Phase S3 — 感知化（C-VLM，7 任务）

### Task 17: 渲染增强 + 页级对比度
**Files:** Modify `examples/.../generator.py`；Test `examples/tests/test_workbench.py` 追加
**Interfaces:** doc["pages"][i] 增 `contrast: float`（scan 页难度高时 <0.5）；渲染按对比度调字色亮度、表格网格/双栏分块/扫描噪点；同种子确定性保持。
- [ ] RED：`test_pages_have_contrast`（难度 0.8 存在 contrast<0.5 页）；`test_render_enhanced_deterministic` → GREEN → gates → Commit `feat: enhanced rendering with page contrast`

### Task 18: image-based extract（工具 + 交付）
**Files:** Modify `examples/.../tools.py`、`generator.py`、`acceptance.py`；Test 追加
**Interfaces:** Produces: 工具 `extract_fields_from_image(doc) -> {"image_ref", "pages":[{page,contrast}]}`（**不返回字段值**）；工具 `submit_extracted_fields(doc_id, fields)`（handler 存 `app._derived` 交付区）；extract 任务改 image-based：验收脚本对照真值判 submit 的交付（从证据链读最后 submit 输出）。`convert_format` dpi>=150 时页 contrast → 1.0。
- [ ] RED：`test_image_extract_tool_returns_no_values`；`test_acceptance_judges_submitted_fields`（错值 fail、真值 pass）→ GREEN → gates → Commit `feat: image-based extract with submit deliverable`

### Task 19: WorkbenchFakeVLM（对比度条件读图）
**Files:** Create `examples/src/examples/docworkbench/fake_vlm.py`；Test `examples/tests/test_fake_vlm.py`
**Interfaces:** Produces: `WorkbenchFakeVLM(corpus, derived)` 实现 ModelAdapter（caps: inputs={TEXT,IMAGE}）；`infer`：从请求消息解析目标 doc（含 image_ref），按**当前页对比度**产出字段——全页 ≥0.5 → 真值；任一 <0.5 → 该页字段确定性错值（hash 扰动）；行为含 tool_call（submit_extracted_fields + 读出值）。Review Focus #3 钉点。
- [ ] RED：`test_fake_vlm_correct_on_high_contrast`；`test_fake_vlm_corrupts_low_contrast`；`test_after_convert_reads_correctly` → GREEN → gates → Commit `feat: contrast-conditional fake VLM`

### Task 20: 感知陷阱进约束表
**Files:** Modify `tools.py HIDDEN_CONSTRAINTS`（+perception_low_contrast 条目与 claim 键）
**Interfaces:** Produces: `{"id":"perception_low_contrast","dimension":"extract","rule":"低对比度页(contrast<0.5)读图前须 convert_format 提升对比度","claim":{"action":"convert_format","param":"dpi","op":">=","value":150}}`；既有约束补 claim 键（audit 用）。
- [ ] RED：`test_perception_constraint_registered` → GREEN → gates → Commit `feat: perception constraint in truth table`

### Task 21: 多模态消息构造 + VLM 路由
**Files:** Modify `agent-core/src/agent_core/runtime/driver.py`（ModelLoopPolicy）、`app.py`（注册双适配器）；Test 追加
**Interfaces:** TaskSpec.constraints `{"vision": true}`（或 kind=`wb:extract-image`）→ 消息附 image content part（`{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`，来自 corpus.assets）；路由 requirements `inputs=[TEXT,IMAGE]`。app：gateway 同时注册文本适配器（EWC env）与 WorkbenchFakeVLM。
- [ ] RED：`test_vision_task_routes_to_vlm`（Fake 双适配器，断言 VLM 收到 image part、文本适配器收不到该任务）→ GREEN → gates → Commit `feat: multimodal message + capability routing`

### Task 22: 感知语料实验接入
**Files:** Modify `generator.py`（extract 任务带 vision 标记进保留集/练习池）；Test 追加
**Interfaces:** `Corpus.tasks` 的 extract 项含 `"image_required": true`；split 保持维度均衡（既有测试适配）。
- [ ] RED：`test_image_extract_in_heldout` → GREEN → gates → Commit `feat: perceptual corpus wired into experiment`

### Task 23: v3 主实验（北极星判定）
**Files:** 归档 `docs/experiments/round4_v3_perceptual.md` + json；必要时 CLI 增 `--fake-vlm` 开关
**Interfaces:** 运行配置：难度 0.8、种子 41-43、FakeVLM 读图 + DeepSeek 驱动。**通过线：explore 臂 extract ≥0.67 且错误晋级率 0 且 comparable**。不达标 → 诊断迭代（课程/门禁调参，代码修正走 mini-TDD）后重跑。
- [ ] Run + 北极星断言 → Commit `docs: v3 perceptual experiment (north star)`

---

## Phase S4 — MCP 双向（6 任务）

### Task 24: mcp 依赖 + MCPClientAdapter
**Files:** Modify `agent-adapters/pyproject.toml`（+`mcp>=1`）；Create `agent-adapters/src/agent_adapters/mcp_client.py`；Test `agent-adapters/tests/test_mcp_client.py`
**Interfaces:** Produces: `MCPClientAdapter(server_cmd: list[str])`（ProtocolAdapter 实现）：`import_capabilities() -> list[CapabilityDescriptor]`（list_tools → schema 映射，side_effect_level="read" 默认）；`bind_all(registry)` 绑 call_tool handler（lazy stdio session，`anyio` 由 SDK 依赖带入）。
- [ ] RED（对 fixture server，见 T25）：`test_import_capabilities_maps_tools`；`test_call_tool_roundtrip` → GREEN → gates → Commit `feat: MCP client adapter`

### Task 25: Python 最小 filesystem server fixture
**Files:** Create `agent-adapters/tests/fixtures/fs_server.py`（官方 SDK FastMCP：`list_dir`/`read_text` 限定根目录）
**Interfaces:** Produces: `python fs_server.py <root>` stdio server；T24/T26 测试经 `sys.executable` 启动。
- [ ] 手测启动握手 → 并入 T24 测试使用 → Commit `test: minimal MCP filesystem server fixture`

### Task 26: client 参考验收
**Files:** Test `agent-adapters/tests/test_mcp_reference.py`
**Interfaces:** 端到端：起 fixture server → import → registry 绑定 → CapabilityExecutor.dispatch → read_text 内容与 tmp 文件一致；工具结果进 ActionResult 证据链。
- [ ] RED → GREEN → gates → Commit `test: MCP client reference acceptance`

### Task 27: MCPServerExporter（暴露子集）
**Files:** Create `agent-adapters/src/agent_adapters/mcp_server.py`；Test `agent-adapters/tests/test_mcp_server.py`
**Interfaces:** Produces: `MCPServerExporter(registry, policy_engine, executor, allowed: set[str] | None = None)`——可暴露集 = 非 irreversible 且 auth_scope 空且（allowed 为 None 或 id ∈ allowed）；每个工具调用构造 ActionRequest → authorize → dispatch → 结果回传；FastMCP 工具 schema 由 descriptor 生成。
- [ ] RED：`test_exports_only_safe_subset`（irreversible/带 scope 不出现）→ GREEN → gates → Commit `feat: MCP server exporter with expose filter`

### Task 28: server 权限门控（Review Focus #4）
**Files:** 同 T27；Test 追加
**Interfaces:** 每调用 PolicyEngine.authorize；不可逆/越权 → 工具返回结构化错误且**不执行**。
- [ ] RED：`test_irreversible_rejected_per_call`（即使强行注册也被 authorize 拒）→ GREEN → gates → Commit `feat: MCP server per-call policy gate`

### Task 29: server e2e
**Files:** Test 追加（fixture client session 连 exporter server）
**Interfaces:** 外部 MCP client 调用导出的 workbench 只读工具 → 结果正确 + ActionRequest/证据落 EventLog。
- [ ] RED：`test_external_client_roundtrip` → GREEN → gates → Commit `test: MCP server e2e with evidence chain`

---

## Phase S5 — 收尾（2 任务）

### Task 30: V2 验收套件
**Files:** `tests_acceptance/test_v2_acceptance.py`
**Interfaces:** 汇总断言：评测门守卫拒保留集；CANDIDATE 默认不可见；感知约束可学（naive 读错→convert 后对）；MCP 不可逆拒绝；北极星报告阈值自动判定（读最新 v3 json 断言 extract≥0.67、错误率 0、comparable）。
- [ ] 全绿 → Commit `test: V2 acceptance suite`

### Task 31: 整分支最终评审
- [ ] review-package → Opus 评审（携带 Review Focus 与 Ruling 台账）→ 分级修复（Critical/Important RED→GREEN）→ minors 台账 → 合并回 master → 推送

## 执行顺序与依赖

S1（T1–T9，其中 T5 计量是 T16/T23 的前置）→ S2（T10–T16）→ S3（T17–T23）→ S4（T24–T29，可与 S3 并行但本计划串行）→ S5。T12 依赖 T5/T10/T11；T23 依赖 S3 全部与 T16 经验。

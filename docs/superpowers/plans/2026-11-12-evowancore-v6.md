# EvoWanCore V6 计划——V5 Ruling 推迟项交付（SSE / resume / LLM 调度）

**Goal:** V5 终评审推迟的三项全部落地：SSE 事件流、HTTP resume 端点、LLM 驱动编排调度。

**纪律沿用 V5 三件套**：superpowers=外层任务循环+终评审；matt=每任务 TDD+全量 pytest+ruff；bmad=story 纪律（task=story，AC=任务测试，red-green-refactor）。

## 边界（先删后做——Musk 五步之 1/2）

- **不引入**新的 pub/sub 基础设施：SSE 用"SDK 侧游标轮询事件日志"最小实现
- **不改** `reserved.py`（同步 Protocol 保持冻结）：LLM 策略走 async-native 接口 + 静态策略适配器
- **不做** LLM 自由文本调度：策略输出必须过白名单校验（未知 agent id 丢弃、非法终止词 fail-safe 终止）——提示注入红线

## Task 1: SSE 事件流端点
**Files:** Modify `agent-sdk/src/agent_sdk/api.py`；Test `agent-sdk/tests/test_sse.py`
**Interfaces:** `GET /runs/{id}/events/stream` → `text/event-stream`：
重放已有事件 → 增量推送 → run 终态且排空后 `event: end` 关闭；
`Last-Event-ID` 头恢复游标；断连（write 异常）干净退出；未知 run → 404。
背压 = aiohttp StreamResponse.write 等待对端排空（有界写缓冲）。
- [ ] RED：`test_sse_streams_live_events`（慢模型下实时收到 ≥3 事件 + end 关闭）
- [ ] RED：`test_sse_unknown_run_404`、`test_sse_last_event_id_resume`
- [ ] GREEN → gates → Commit

## Task 2: HTTP resume 端点（image_urls 注入协议）
**Files:** Modify `api.py` + `models.py`（`ResumeRunRequest`）；Test `agent-sdk/tests/test_resume.py`
**Interfaces:** `POST /runs/{id}/resume` body `{"image_urls": {"<doc_id>": "<url>"}}`
→ 调 `runtime.resume(run_id, image_urls=...)` → RunStatusResponse。
协议：doc_id → URL 映射由 caller 持有（corpus 在 caller 侧，内核只存 image_doc_id 引用）。
未知 run → 404；终态 run → 409（IllegalTransition）。
- [ ] RED：`test_resume_endpoint_e2e`（crash-sim → POST resume → succeeded + ActionOutcomeUnknown）
- [ ] GREEN → gates → Commit

## Task 3: LLMCompositionPolicy（动态选人）
**Files:** Modify `agent-core/src/agent_core/orchestration.py`；Test `agent-core/tests/test_orchestration.py` 追加
**Interfaces:** `LLMCompositionPolicy(planner)`——`async select_participants(task, agents, context)`
prompt = 任务 + 各 agent 能力 + 已执行轮次摘要 → planner 返回 JSON id 列表；
**白名单校验**：只保留已知 id，全无效 → 空列表（本轮不派人）。
- [ ] RED：`test_llm_policy_selects_dynamically`（第 1 轮选 A、A 完成后第 2 轮改选 B）
- [ ] RED：`test_llm_policy_rejects_phantom_agents`（幻觉 id 丢弃）
- [ ] GREEN → gates → Commit

## Task 4: ReplanningOrchestrator（多轮循环）
**Files:** Modify `orchestration.py`；Test 追加
**Interfaces:** `ReplanningOrchestrator(policy, runtimes, max_rounds=4).orchestrate(task, agents)`
每轮：policy 选人（带 context）→ 并行执行（异常隔离）→ 更新 context →
policy.decide_termination → continue 则重规划下一轮（已成功者不重跑）。
`StaticPolicyAdapter` 包住同步 CapabilityCompositionPolicy 复用同一循环。
- [ ] RED：`test_replanning_two_rounds`（轮 1 只 A，轮 2 只 B，各自成功）
- [ ] RED：`test_replanning_max_rounds_bound`
- [ ] GREEN → gates → Commit

## Task 5: V6 验收 + 终评审
**Files:** `tests_acceptance/test_v6_acceptance.py`
- [ ] SSE/resume/LLM 编排三项端到端 + 红线（幻觉 id、终态 409、无 key 泄漏）
- [ ] 全绿 → 子代理终评审 → 修复 → 合并 master → push

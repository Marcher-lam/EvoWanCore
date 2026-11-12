# EvoWanCore V5 实现计划（生态层）

> **For agentic workers:** superpowers:executing-plans 逐任务执行。

**Goal:** 协议桩全部变为可运行实现——服务化/编排/A2A/CUA/记忆清除/Realtime 接入点。

**Architecture:** agent-sdk 包提供 aiohttp HTTP API；agent-core 增 orchestration/
transport/environment/purge 四模块；全部 Protocol 桩签名不变、只加实现。

## Global Constraints

- 继承全部：每任务 TDD/全量门（pytest+ruff）/每任务提交
- 不引入 FastAPI（pydantic 版本冲突风险）；用 aiohttp
- 协议桩签名不变——实现类实现 Protocol 接口，不修改 reserved.py

## Review Focus

1. SDK API 端到端（HTTP → 事件流 → 终态）
2. 编排：多 Agent 能力匹配 + 终止正确
3. A2A：跨 AgentTransport roundtrip
4. CUA：observe/act 可测试
5. 记忆清除后不可检索 + 审计留痕

---

## Phase S1 — 服务化 + SDK（3 任务）

### Task 1: agent-sdk 包骨架 + HTTP API 端点
**Files:** Create `agent-sdk/pyproject.toml`、`agent-sdk/src/agent_sdk/__init__.py`、
`api.py`、`models.py`；Test `agent-sdk/tests/test_api.py`
**Interfaces:** Produces:
- `AgentSDKServer(app_factory, host, port)` — aiohttp 服务器
- `POST /runs` → 提交任务（body: task spec + agent spec）→ run_id
- `GET /runs/{id}` → run 状态
- `GET /runs/{id}/events` → 事件列表（JSON）
- `POST /runs/{id}/cancel` → 取消
- `DELETE /memory/{namespace}/{id}` → 记忆清除（T5）
- [ ] RED：`test_submit_and_get_run`（HTTP roundtrip：POST → GET → 终态）
- [ ] GREEN → gates → Commit `feat: agent-sdk HTTP API`

### Task 2: 事件流 SSE 端点
**Files:** Modify `api.py`；Test 追加
**Interfaces:** `GET /runs/{id}/events/stream` → SSE 长连接推送 RuntimeEvent
- [ ] RED：`test_event_stream_sse`（客户端收到 ≥1 事件）
- [ ] GREEN → gates → Commit `feat: SSE event stream`

### Task 3: explore 端点
**Files:** Modify `api.py`；Test 追加
**Interfaces:** `POST /explore` → 触发探索（body: policy）→ ExplorationReport
- [ ] RED：`test_explore_endpoint`（HTTP 触发 → 报告返回）
- [ ] GREEN → gates → Commit `feat: explore endpoint`

## Phase S2 — 多 Agent 编排（2 任务）

### Task 4: CompositionPolicy 实现
**Files:** Create `agent-core/src/agent_core/orchestration.py`；Test `agent-core/tests/test_orchestration.py`
**Interfaces:** Produces: `CapabilityCompositionPolicy`——`select_participants(task, agents)`
按 agent 声明的 capabilities 与 task kind 匹配返回 agent id 列表；
`decide_termination(task, context)` 检查全部参与者是否到达终态。
- [ ] RED：`test_select_by_capability`（匹配/不匹配/空）
- [ ] GREEN → gates → Commit `feat: CompositionPolicy implementation`

### Task 5: Orchestrator（多 Agent 并行执行）
**Files:** Modify `orchestration.py`；Test 追加
**Interfaces:** `Orchestrator(policy, runtimes: dict[str, AgentRuntime])`——
`orchestrate(task, agents)` → 选择参与者 → asyncio.gather 并行 submit → 终态汇总。
- [ ] RED：`test_parallel_orchestration`（2 agent 各自完成各自子任务）
- [ ] GREEN → gates → Commit `feat: multi-agent Orchestrator`

## Phase S3 — A2A（2 任务）

### Task 6: AgentTransport 实现（HTTP JSON 信封）
**Files:** Create `agent-core/src/agent_core/transport.py`；Test `agent-core/tests/test_transport.py`
**Interfaces:** Produces: `HTTPAgentTransport(base_url)`——
`send(envelope)` POST JSON 到远端 `/a2a`；`receive()` 从本地队列弹出。
- [ ] RED：`test_transport_roundtrip`（send → 远端收到 envelope → reply → receive）
- [ ] GREEN → gates → Commit `feat: HTTP AgentTransport`

### Task 7: A2A 信封协议模型 + 注册表
**Files:** Modify `transport.py`；Test 追加
**Interfaces:** `A2AEnvelope(from_agent, to_agent, message_type, payload, reply_to)`；
`AgentRegistry` — 注册/发现 Agent（name + capabilities + endpoint）。
- [ ] RED：`test_envelope_schema`、`test_registry_discover`
- [ ] GREEN → gates → Commit `feat: A2A envelope + agent registry`

## Phase S4 — CUA（2 任务）

### Task 8: EnvironmentAdapter 实现（HTTP 桩）
**Files:** Create `agent-core/src/agent_core/environment.py`；Test `agent-core/tests/test_environment.py`
**Interfaces:** `HTTPEnvironmentAdapter(base_url)`——
`observe()` GET `/observe` 返回 {dom, screenshot_url}；
`act(action)` POST `/act` 执行 {click, type, scroll, screenshot} → result。
- [ ] RED：`test_observe_returns_dom`、`test_act_click`
- [ ] GREEN → gates → Commit `feat: HTTP EnvironmentAdapter`

### Task 9: CUA 能力注册（click/type/screenshot 工具）
**Files:** Modify `environment.py`；Test 追加
**Interfaces:** `register_cua_tools(registry, adapter)` — 把 observe/act 包装为
CapabilityDescriptor + handler 注册进能力目录（模型可选用）。
- [ ] RED：`test_cua_tools_discoverable`
- [ ] GREEN → gates → Commit `feat: CUA tools as capabilities`

## Phase S5 — 收尾（2 任务）

### Task 10: 记忆物理清除
**Files:** Create `agent-core/src/agent_core/memory/purge.py`；Modify `store.py`；Test 追加
**Interfaces:** `MemoryStore.delete(namespace, record_id)` — 物理 DELETE + 审计事件。
- [ ] RED：`test_purge_deletes_and_audits`
- [ ] GREEN → gates → Commit `feat: memory physical purge`

### Task 11: Realtime key 接入点
**Files:** Create `agent-adapters/src/agent_adapters/realtime_openai.py`；Test 追加
**Interfaces:** `OpenAIRealtimeAdapter(endpoint, api_key)` — env 缺失时 ImportError 提示；
骨架实现 open_session/send_text/close_session 走 OpenAI Realtime API。
- [ ] RED：`test_realtime_adapter_no_key_raises`（无 key → 明确错误）
- [ ] GREEN → gates → Commit `feat: OpenAI Realtime adapter skeleton`

## Phase S6 — 收尾（1 任务）

### Task 12: V5 验收套件 + 终评审
- [ ] 全绿 → 子代理终评审 → 修复 → 合并 → push

## 执行顺序

S1 → S2 → S3 → S4 → S5 → S6（S2-S4 可穿插）

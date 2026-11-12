# EvoWanCore V7 计划——清债轮（backlog Low + 未验证骨架补全）

**Goal:** V6 Ruling 记录的 backlog 全部闭环 + Realtime 骨架协议级验证。
**纪律沿用三件套**：superpowers=外层循环+终评审；matt=TDD+全量 pytest+ruff；bmad=story。

**非目标（维持）**：CUA 真浏览器（Playwright 重依赖）；A2A 标准 Google spec（待生态）；
TS SDK（另行立项）。

## Task 1: runtime 孤儿任务收口
**Files:** `agent-core/src/agent_core/runtime/runtime.py`；Test 追加 test_runtime_loop.py
**Interfaces:** `_dispatch_with_cancel` 外层取消时 dispatch_task/cancel_wait 两个子任务
必须 cancel+reap（不再泄漏 pending 任务）。
- [ ] RED：`test_dispatch_cancel_reaps_orphan_tasks`（外层取消后无 pending 任务残留）
- [ ] GREEN → gates → Commit

## Task 2: eventlog seq 游标 + SSE 心跳帧
**Files:** `runtime/eventlog.py`（`iter_events_with_seq(correlation_id, after_seq)`）；
`runtime/runtime.py`（`events_with_seq` 公有透传）；`agent-sdk/api.py`（游标轮询 + `: ping`）
**Interfaces:** SSE 改 `WHERE seq > ?` 增量读（O(n²)→O(n)）；空闲超 heartbeat_s
发 `: ping` 注释帧（保活 + 死对端检测）。heartbeat_s 默认 5.0。
- [ ] RED：`test_sse_heartbeat_on_idle`（悬挂 run + heartbeat_s=0.05 → 收到 ping 帧）
- [ ] RED：`test_events_with_seq_cursor`（eventlog 单元：只返回 seq 之后事件）
- [ ] GREEN → gates → Commit

## Task 3: port=0 动态端口（CI 并行去碰撞）
**Files:** `agent-sdk/api.py`（start 后回读实际端口）；`agent-core/transport.py`
（InboundA2AHandler.port 回读）；全部固定端口测试改造
- [ ] RED：`test_port_zero_gets_ephemeral`
- [ ] GREEN → 全测试改造 → gates → Commit

## Task 4: A2A 入站 bearer 鉴权
**Files:** `agent-core/transport.py`；Test 追加
**Interfaces:** `InboundA2AHandler(port, handler, auth_token=None)`——设置后
POST /a2a 必须带 `Authorization: Bearer <token>`，否则 401；
`HTTPAgentTransport(base_url, auth_token=None)` 同步发送头。默认 None=开放（回环兼容）。
- [ ] RED：`test_a2a_auth_rejects_missing_token` / `test_a2a_auth_roundtrip`
- [ ] GREEN → gates → Commit

## Task 5: Realtime 协议级 mock 验证
**Files:** `agent-adapters/tests/test_realtime_openai.py` 追加
**Interfaces:** 本地 websockets mock server 说 RealtimeSessionAdapter 的 provider
事件方言 → `OpenAIRealtimeAdapter.open_session/send_text/close_session` 真跑通
一条 socket 链路（骨架从"未验证"变"协议验证过"）。
- [ ] RED：`test_realtime_skeleton_over_mock_socket`
- [ ] GREEN → gates → Commit

## Task 6: V7 验收 + 终评审 + 合并 + push

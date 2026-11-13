# EvoWanCore V9 计划——V8 backlog 清零

**Goal:** V8 Ruling 在案 backlog 三项落地；streaming/push/tasks 维持不做。
**纪律三件套不变。**

## Task 1: A2A Notification 严格语义
**Files:** `agent-core/src/agent_core/a2a_standard.py`；Test 追加
**Interfaces:** JSON-RPC 请求无 `id` = Notification → 处理但不回包（HTTP 204）；
含错误（method not found 等）同样 204。客户端 `notifyText()`（期望 204）。
- [ ] RED：无 id → 204 无 body + handler 副作用可见；无 id 未知 method → 204
- [ ] GREEN → gates → Commit

## Task 2: TS SDK fetch 注入点（keep-alive 说明 + DI）
**Files:** `agent-sdk-ts/src/index.ts`
**Interfaces:** `new AgentSDKClient(baseUrl, { fetch? })`——默认全局 fetch。
Node 的 fetch 走 undici 连接池（默认 keep-alive），浏览器同源池化；
需要自定义 dispatcher（keepAliveTimeout/pipelining 调优）的 Node 用户
经注入点传 `undici` Agent 感知的 fetch。文档注释写明。
- [ ] tsc + smoke 复验 → Commit

## Task 3: Playwright 真站点适配（iframe / shadow DOM / networkidle）
**Files:** `agent-adapters/src/agent_adapters/playwright_env.py`；Test 追加
**Interfaces:**
- 目标解析：主 frame 未命中 → 遍历子 frame（iframe 内元素可 act）
- open shadow DOM：Playwright CSS 原生穿透（验证性测试）
- `observe(settle="networkidle")` 可选等待（默认 None 不等）
- 测试载体升级：本地 aiohttp HTTP 站点（弃 data: URL）
- [ ] RED：iframe 按钮点击生效 / shadow 按钮 observe+click / networkidle 等到懒加载文本
- [ ] GREEN → gates → Commit

## Task 4: V6 过时 Ruling 划线 + V9 验收 + 终评审 + 合并 + push
- [ ] V6 计划中已被 V7 清掉的遗留段标注（文档与事实一致）
- [ ] 验收：Notification 204 / iframe+shadow+networkidle 真 HTTP 闭环 / smoke PASS
- [ ] 子代理终评审 → 修复 → 合并 → push

## 维持 Ruling（不做）
- A2A streaming/push/tasks 生命周期：message/send 返回 Message（messaging-mode
  agent）本身合规；task-mode 待生态需求出现再立项。

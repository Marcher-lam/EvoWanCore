# EvoWanCore V8 计划——三块"非目标"转正（A2A 标准 / TS SDK / Playwright CUA）

**Goal:** 把 V1-V7 全部登记为非目标的三块落地为最小可信交付。
**纪律三件套不变**；范围裁剪（Musk 1/2 步先行）：

- **A2A Google spec**：只做核心子集——Agent Card（`/.well-known/agent-card.json`）
  + JSON-RPC 2.0 `message/send`。不做 streaming（SSE）、push notifications、
  tasks 全生命周期、artifacts——Ruling 记录，生态真正用到再加。
- **TS SDK**：只做**客户端**（调 agent-sdk HTTP API），不做服务端移植。
  TypeScript + 零运行时依赖（fetch/AbortController 原生）。验收 = tsc 编译
  + 针对真 Python server 的 node 冒烟 e2e。
- **CUA 真浏览器**：`PlaywrightEnvironmentAdapter`（实现既有
  EnvironmentAdapter Protocol），chromium headless。测试 skip-if-no-browser
  （CI 无浏览器环境不红）。

## Task 1: A2A 标准 Agent Card + JSON-RPC message/send
**Files:** Create `agent-core/src/agent_core/a2a_standard.py`；Test `agent-core/tests/test_a2a_standard.py`
**Interfaces:**
- `AgentCard`（pydantic：name/description/url/version/capabilities/skills）
- `A2AStandardEndpoint(card, handler, auth_token=None)`——aiohttp：
  `GET /.well-known/agent-card.json`；`POST /` JSON-RPC 2.0
  （method=message/send；params.message.parts[].text ↔ A2AEnvelope.payload.text）
- `A2AStandardClient(base_url, auth_token=None).send_text(text)` → agent 回复文本
- JSON-RPC 错误规范：-32700 parse / -32601 method not found；鉴权 401 复用
- [ ] RED：card 发布 / message/send 往返 / 未知 method -32601 / 错 token 401
- [ ] GREEN → gates → Commit

## Task 2: TS SDK 客户端（agent-sdk-ts/）
**Files:** `agent-sdk-ts/{package.json,tsconfig.json,src/index.ts,src/types.ts}`
+ `smoke/run.mjs`（对真 Python server 的 e2e）
**Interfaces:** `AgentSDKClient`（submitRun/getRun/listEvents/cancelRun/
resumeRun/purgeMemory/streamEvents(fetch 流式解析 SSE)）+ 全类型导出。
- [ ] tsc --noEmit 过；node 冒烟（起 Python server → submit → 轮询终态 →
  SSE 收 ≥3 事件 + end）全绿
- [ ] Commit

## Task 3: Playwright 真 CUA adapter
**Files:** agent-adapters optional extra `[browser]`；
Create `agent-adapters/src/agent_adapters/playwright_env.py`；Test 追加
**Interfaces:** `PlaywrightEnvironmentAdapter(headless=True)`——
observe() → {dom(可读性文本), screenshot_url(data: URL)}；act() →
click/type/scroll/screenshot 真执行；supports_snapshot()=True；
`async aclose()`。浏览器启动失败 → 测试 skip（CI 兼容）。
- [ ] RED：本地 data: URL 页面 observe 看到文本 / click 按钮后 DOM 变化 /
  type 进 input 后 value 反映
- [ ] GREEN → gates → Commit

## Task 4: V8 验收 + 终评审 + 合并 + push

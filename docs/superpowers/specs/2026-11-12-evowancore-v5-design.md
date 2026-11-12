# EvoWanCore V5 设计文档（生态层：服务化+编排+A2A+CUA+收尾）

- 日期：2026-11-12
- 状态：待评审 → 待用户确认
- 前置：V1-V4 全部合并 master（真模型北极星满分 explore 1.000）
- 标注：**[已确认]** = 用户拍板；**[我的裁量]** = 待用户复核

---

## 1. 意图与动机

V5 命题：**把已验证的 Agent 内核开放为生态——服务化、多 Agent 协作、跨进程通信、
计算机使用——同时清偿最后的遗留项（记忆物理清除、Realtime key 接入点）。**

V1-V4 建成了完整闭环（258+ 测试、真模型满分、红线 0），但所有能力被锁在
Python 进程内。V5 的四个协议桩（reserved.py）全部变为可运行实现。

## 2. 目标

**A 服务化 + SDK**（~4 任务）
1. `agent-sdk` 包：FastAPI HTTP API 暴露 submit/cancel/resume/events/explore
2. RESTful 端点映射内核操作（POST /runs、GET /runs/{id}/events 等）
3. 类型化请求/响应模型（复用 Pydantic 契约）

**B 多 Agent 编排**（~3 任务）
4. `CompositionPolicy` 实现：基于能力匹配的参与者选择 + 终止判定
5. `Orchestrator`：将 CompositionPolicy 接入 AgentRuntime——多 Agent 并行/串行执行

**C A2A（Agent 间通信）**（~3 任务）
6. `AgentTransport` 实现：HTTP JSON 信封协议（跨进程 Agent 互发消息）
7. 简单注册表：Agent 发现 + 能力声明

**D CUA（计算机使用）**（~3 任务）
8. `EnvironmentAdapter` 实现：HTTP 网页操作（observe=截屏/DOM、act=点击/输入）
9. 把 CUA 环境桥接为能力（`click`/`type`/`screenshot` 工具注册进能力目录）

**E 收尾**（~2 任务）
10. 记忆物理清除（SQLite DELETE + 生命周期审计事件）
11. Realtime 真供应商接入点（env key 检测 + OpenAI Realtime API 适配器骨架）

## 3. 约束

- 继承全部（TDD/全量门/ruff 100/每任务提交）
- HTTP 层不引入重依赖：标准库 `http.server` 或 `aiohttp`（已可用 uv 装轻量）
- A2A 协议用 JSON 信封（对齐 AgentTransport Protocol 签名，不引入外部标准）
- CUA 不做完整浏览器驱动——HTTP 级 observe/act（DOM/截图桩），真浏览器待扩展

## 4. 方案选择

| 决策 | 选择 | 理由 |
|---|---|---|
| HTTP 框架 | aiohttp（轻量异步） | uv 可装；FastAPI 引 pydantic 版本冲突风险 |
| 编排模式 | 能力匹配+静态策略 | V5 不做 LLM 驱动调度（V6+）|
| A2A 协议 | JSON 信封 over HTTP | 对齐 Protocol 桩签名；标准 A2A 待生态成熟 |
| CUA 范围 | HTTP observe/act 桩 | 真浏览器需 Playwright/Selenium 重依赖 |
| SDK 语言 | Python only | TS SDK 待服务化验证后 |

## 5. 架构增量

```
agent-sdk/
  src/agent_sdk/
    api.py          # aiohttp HTTP API（submit/cancel/events/explore）
    models.py       # 请求/响应 Pydantic 模型
    server.py       # 启动/配置

agent-core/
  src/agent_core/
    orchestration.py  # CompositionPolicy 实现 + Orchestrator
    transport.py      # AgentTransport 实现（HTTP JSON 信封）
    environment.py    # EnvironmentAdapter 实现（HTTP observe/act）
    memory/purge.py   # 记忆物理清除
```

## 6. 验收

- SDK API 端到端：HTTP 提交任务 → 事件流 → 终态（集成测试）
- 编排：多 Agent 按能力分配 → 并行执行 → 终止判定正确
- A2A：两个进程互发消息（跨 AgentTransport roundtrip）
- CUA：observe 返回 DOM 快照、act 执行操作（桩环境集成测试）
- 记忆清除：DELETE 后记录不可检索 + 审计事件记录
- 全量测试保持绿 + ruff clean

## 7. 留给实现者

HTTP 端点命名细节、编排策略参数、A2A 信封 schema 字段、CUA 桩环境脚本

## 8. 裁量清单

| # | 裁量 | 默认 |
|---|---|---|
| 1 | HTTP 框架 | aiohttp（轻量、uv 可装） |
| 2 | CUA 范围 | HTTP 桩（DOM+截图），非真浏览器 |
| 3 | A2A 协议 | 自定义 JSON 信封，非标准 A2A |
| 4 | SDK 语言 | Python only（TS 待验证后） |
| 5 | 编排策略 | 能力匹配+静态（非 LLM 驱动） |

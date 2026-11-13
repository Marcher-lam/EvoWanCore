# EvoWanCore 交接文档（V10 · 2026-11-13）

> 进化式 Agent 系统：让系统学会它没学会的东西。
> V1 内核闭环 → V2 学习/感知/MCP → V3 真 27b 北极星 → V4 extract 1.000 →
> V5 生态层 → V6 推迟项 → V7 清债 → V8 非目标转正 → V9 backlog 清零 →
> V10 工程面收口。

## 1. 仓库结构（uv workspace + TS SDK）

| 包 | 职责 |
|---|---|
| `agent-core` | 内核：runtime（执行循环/检查点/resume）、能力目录、事件日志、状态/证据存储、记忆（含物理清除+审计）、orchestration（静态+LLM 重规划）、A2A（内部信封 + Google spec 标准）、transport、environment（CUA） |
| `agent-sdk` | HTTP API 服务（aiohttp）：submit(QUEUED)/get/events/SSE 流/cancel/resume/explore/memory purge；可选 bearer 鉴权 |
| `agent-adapters` | 模型/协议适配：openai_compat、lmstudio_vlm、realtime 会话、OpenAI Realtime 接入点、MCP 双向、Playwright 真 CUA（extra: browser） |
| `agent-explore` | 探索 + B 闭环：LLM 记忆 reflector、DGM 技能演化（verify-and-revise、lineage） |
| `agent-evolve` | 技能门禁/沙箱/注册表：最小 env、网络默认拒绝、沙箱执行 |
| `agent-devtools` | 开发工具 |
| `examples/docworkbench` | 北极星应用：VLM 文档工作台（27b 北极星 1.000 的载体） |
| `agent-sdk-ts` | TypeScript 客户端 SDK（零运行时依赖，fetch + SSE + authToken） |
| `tests_acceptance` | V2-V10 验收套件（每版一文件） |

## 2. 协议实现对照（reserved.py 四桩全落地）

| Protocol 桩 | 实现 | 备注 |
|---|---|---|
| `ProtocolAdapter` | `agent_adapters/mcp_client.py` | V2 |
| `CompositionPolicy` | `agent_core/orchestration.py`（静态 + `LLMCompositionPolicy` 白名单防注入 + `ReplanningOrchestrator`） | V5/V6 |
| `AgentTransport` | `agent_core/transport.py`（HTTP 信封 + registry + bearer）+ `a2a_standard.py`（Google spec：Agent Card camelCase + JSON-RPC message/send + Notification 204） | V5/V8/V9 |
| `EnvironmentAdapter` | `environment.py`（HTTP 桩）+ `playwright_env.py`（真 chromium：iframe 穿透/open shadow/networkidle） | V5/V8/V9 |

## 3. 如何跑

```bash
uv sync                                # Python 全依赖（root 已带 agent-adapters[browser]）
uv run playwright install chromium    # 真浏览器测试需要（无则 skip）
uv run ruff check .                    # lint
uv run pytest -q                       # 全量 349+（零网络依赖；LM Studio 仅实验脚本用）
cd agent-sdk-ts && npm install && npx tsc -p tsconfig.json && node smoke/run.mjs
                                       # TS SDK 编译 + 对真 Python server 冒烟
```

CI：`.github/workflows/ci.yml`（python: ruff+pytest+chromium；ts: tsc+smoke）。
发布：`.github/workflows/release.yml`（手动触发，构建+校验；publish 需凭据）。

## 4. 北极星与红线

- **北极星**（V4 达成）：真 27b VLM `explore 1.000 / extract 1.000`，红线 0，
  comparable（双臂同预算）。关键机制：terminal-yield、vision-update（convert 后
  切 2x 图）、祈使式记忆渲染、pre-gate audit veto、doc_id 过滤。
- **安全红线**（不可破）：
  1. 任何 key 不写入被跟踪文件（验收扫描 `sk-` 形态；曾泄漏的 key 已建议轮换）
  2. 能力副作用如实声明（write 不得伪装 read；CUA click/type=`write`+`auth_scope=["cua:act"]`）
  3. Skill 沙箱：最小 env、网络默认拒绝、路径穿越防护
  4. LLM 编排输出过白名单（幻觉 agent id 丢弃、非法终止词 fail-safe terminate）
  5. token 比较恒时（hmac.compare_digest）

## 5. 设计取舍索引（Ruling，按版本）

- [V5 计划](superpowers/plans/2026-11-12-evowancore-v5.md)：SSE/resume/LLM 调度推迟 V6（已清偿）
- [V6 计划](superpowers/plans/2026-11-12-evowancore-v6.md)：终评审修复记录
- [V7 计划](superpowers/plans/2026-11-12-evowancore-v7.md)：清债轮 Ruling
- [V8 计划](superpowers/plans/2026-11-12-evowancore-v8.md)：A2A streaming/push/tasks 不做；Playwright 嵌套 shadow 递归/可见性过滤遗留
- [V9 计划](superpowers/plans/2026-11-13-evowancore-v9.md)：backlog 清零；task-mode 待生态
- [V10 计划](superpowers/plans/2026-11-13-evowancore-v10.md)：发布为"就绪"不"发布"

## 6. 交接须知

- 分支：`master` 为唯一长期分支；每版 `feat/vN-*` 合并后保留
- 测试即验收：每版验收套件在 `tests_acceptance/test_vN_acceptance.py`
- 方法论：superpowers（外层循环+终评审 subagent）/ matt（TDD+ruff+全量门禁）/
  bmad（story 纪律）——终评审 MUST 模式已连续抓回真雷（V6 SSE 尾丢事件、
  V8 A2A snake_case、V9 划线未命中），**不要跳过**
- LLM 供应商：LM Studio 本地（OpenAI 兼容，`reasoning_effort=low` 提速 4-10x）；
  实验脚本在 examples/ 与 docs/experiments/，不在 pytest 内

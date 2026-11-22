# EvoWanCore 交接文档（V18 · 2026-11-23）

> 进化式 Agent 系统：让系统学会它没学会的东西。
> V1 内核闭环 → V2 学习/感知/MCP → V3 真 27b 北极星 → V4 extract 1.000 →
> V5 生态层 → V6 推迟项 → V7 清债 → V8 非目标转正 → V9 backlog 清零 →
> V10 工程面收口 → V11-12 多模态 RAG → V13 平台/应用分层 → V14-15 crew
> 四能力 → V16 双应用全景 → V17 工具优先引导 → V18 反饥饿探索闭环。

## 0. 架构原则（V13 确立，最高优先级）

**EvoWanCore = 平台**（agent-core/sdk/adapters/explore/evolve）；
**应用 = 基于平台的二次开发**（examples/ 下的 docworkbench / mrag / crew）。
应用可依赖平台、绝不侵入平台——平台代码零应用概念。
红线由常驻测试守卫（`examples/tests/conftest.py::assert_platform_clean`，
git grep 平台 7 路径，cwd 锚定 + rc 语义不空过）。

## 1. 仓库结构（uv workspace + TS SDK）

| 包 | 职责 |
|---|---|
| `agent-core` | 内核：runtime（执行循环/检查点/resume/submit_bg）、能力目录、事件日志（seq 游标）、状态/证据存储、记忆（物理清除+审计）、orchestration（静态+LLM 重规划）、A2A（内部信封 + Google spec 标准 + Notification 204）、transport（bearer）、environment（HTTP CUA 桩） |
| `agent-sdk` | HTTP API 服务（aiohttp）：submit(QUEUED)/get/events/SSE 流（心跳+游标）/cancel/resume/explore/memory purge；bearer 鉴权（恒时比较）；**extra_routes 通用扩展点**（应用挂自有路由吃平台鉴权/守卫）+ `app[SDK_COMPONENTS]` 组件注入 |
| `agent-adapters` | openai_compat、lmstudio_vlm、realtime 会话、OpenAI Realtime 接入点、MCP 双向、Playwright 真 CUA（iframe 穿透/open shadow/networkidle；extra: browser） |
| `agent-explore` | 探索 + B 闭环：LLM 记忆 reflector、DGM 技能演化（verify-and-revise、lineage） |
| `agent-evolve` | 技能门禁/沙箱/注册表：最小 env、网络默认拒绝、合同门禁 |
| `agent-devtools` | 开发工具 |
| `agent-sdk-ts` | TypeScript 客户端 SDK（零运行时依赖；fetch DI + authToken + request() 通用消费） |
| `examples/docworkbench` | **四能力全景应用**：北极星基准（真 27b explore/extract 1.000）+ V16 编排层（四维度选人/并行/重派/审计） |
| `examples/mrag` | **多模态 RAG 应用**：页面图像 VLM 描述 + 向量检索 + 带引用生成；能力目录 + extra_routes 双形态暴露 |
| `examples/crew` | **多 Agent 自进化团队应用**：探索(curriculum/gap/反例/**prober 反饥饿**)+学习(B闭环祈使记忆)+编排(三阶段流水线)+进化(真门禁技能)；Fake 剧本测试 + 真模型入口 |
| `tests_acceptance` | 验收套件（v2/v3/v5-v8 各一文件；v9-v16 追加） |

## 2. 协议实现对照（reserved.py 四桩全落地）

| Protocol 桩 | 实现 | 备注 |
|---|---|---|
| `ProtocolAdapter` | `agent_adapters/mcp_client.py` | V2 |
| `CompositionPolicy` | `orchestration.py`（静态 + `LLMCompositionPolicy` 白名单防注入 + `ReplanningOrchestrator` 多轮重规划） | V5/V6 |
| `AgentTransport` | `transport.py`（HTTP 信封+registry+bearer）+ `a2a_standard.py`（Google spec：Card camelCase + JSON-RPC + Notification 204 严格语义） | V5/V8/V9 |
| `EnvironmentAdapter` | `environment.py`（HTTP 桩）+ `playwright_env.py`（真 chromium） | V5/V8/V9 |

## 3. 如何跑

```bash
uv sync                                # root 已带 agent-adapters[browser]
uv run playwright install chromium    # 真浏览器测试（无则 skip）
uv run ruff check .                    # lint
uv run pytest -q                       # 全量 414+（零网络；LM Studio 仅实验脚本）
cd agent-sdk-ts && npm ci && npx tsc -p tsconfig.json && node smoke/run.mjs
uv run python -m examples.mrag.realrun --rebuild      # MRAG 真链路（27b+embedding）
uv run python -m examples.crew.realrun_crew --waves 3 --tool-first --probe # crew 真模型全链（后台长跑，caffeinate 防休眠）
```

CI：`.github/workflows/ci.yml`（python: ruff+pytest+chromium；ts: npm ci+tsc+smoke，
SHA pin/权限最小化/锁文件缓存）。发布：`release.yml`（手动；逐包构建校验；publish 需凭据）。

## 4. 双应用四能力矩阵（V16 交付）

| 应用 | 探索 | 学习 | 编排 | 进化 | 模型 |
|---|---|---|---|---|---|
| docworkbench | ✅ ExplorationController 全量 | ✅ 真 LLM reflector→审计→祈使记忆 | ✅ V16 编排层（四维度选人/并行/重派/审计） | ✅ DGM 真反射器 | ✅ 真 27b（1.000 基准，口径零改动） |
| crew | ✅ curriculum/gap_driven/counterexample + **prober 反饥饿（frontier+边界探针）** | ✅ B 闭环（稳定 id 规则+祈使渲染+学习增益可量化 0 拒；探针持续供料） | ✅ 三阶段流水线（classify→extract→audit 打回） | ✅ 真门禁技能（多样本+反例合同锁边界作弊） | 真模型入口（UserRoleGuard+str 容错；V17 tool-first + V18 probe 全量曲线 realrun） |

## 5. 北极星与红线

- **北极星**（V4）：真 27b `explore/extract 1.000`，红线 0，comparable。
  关键机制：terminal-yield、vision-update、祈使式记忆渲染、pre-gate audit veto、
  doc_id 过滤。V16 编排为加法层——原 solve/experiment 路径文件零改动。
- **安全红线**：key 不入仓（CI 扫描）；副作用如实声明；沙箱最小 env/网络拒绝；
  LLM 编排白名单；token 恒时比较；**平台零侵入**（conftest 红线测试）。

## 6. 设计取舍索引（Ruling）

- [V5-V10](superpowers/plans/2026-11-12-evowancore-v5.md)～[v10](superpowers/plans/2026-11-13-evowancore-v10.md)：生态层→清债→转正→backlog→工程面
- [V11 MRAG](superpowers/plans/2026-11-16-evowancore-v11-mrag.md)：PDF 解析非目标（corpus 供图）；向量库后换
- [V12](superpowers/plans/2026-11-16-evowancore-v12-mrag-http.md)→V13 重构：mrag 端点从 SDK 移入应用层（平台只留 extra_routes 通用件）
- [V14 crew](superpowers/plans/2026-11-16-evowancore-v14-crew.md)：门禁=contract_test（DualRunner 对 Fake 无意义）
- [V15 crew 全景](superpowers/plans/2026-11-16-evowancore-v15-crew-full.md)：三角色共单 runtime 串行（并行需 task 键控）
- [V16 双全景](superpowers/plans/2026-11-16-evowancore-v16-dual-full.md)：crew 真模型两怪癖（user-role 400 / str arguments）已修+回归锁定
- [V17 工具优先](examples/src/examples/crew/realrun_crew_report.md)：goal 注入 TOOL-FIRST；发现"变强饿死学习"（批全过→反射器断粮）
- [V18 反饥饿](superpowers/plans/2026-11-23-evowancore-v18-antistarve.md)：prober 主动合成难例（frontier amount_words+边界 counterexample）持续供料反射器；探针失败按合成真值归因（grader 字段盲猜=饥饿第二形态）；真跑实证 rule_amount_words 为探针产物（批从未含该模式）、wave3 不再回落（0.33→0.50）、记忆 1→4 条

## 7. 交接须知

- 分支：`master` 唯一长期分支；每版 `feat/vN-*` 合并后保留
- 测试即验收：`tests_acceptance/test_vN_*` + 各应用 tests；**断言必须可失败**
  （V16 教训：前缀永真断言放过 probe 空 id 真雷）
- 方法论：superpowers（外层循环+终评审 subagent——连续 11 版抓回真雷，勿跳）/
  matt（TDD+ruff+全量门禁，先看输出再 commit）/ bmad（story 纪律）
- LLM：LM Studio 本地（qwen3.8-27b-mlx；`reasoning_effort=low`；
  **qwen3.8 要求 messages 含 user 角色**——UserRoleGuard 已内置）；
  embedding `text-embedding-qwen3-embedding-0.6b`
- 真模型实验均长跑（realrun 30min+）：后台执行 + 报告落盘模式

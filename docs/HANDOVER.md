# EvoWanCore 交接文档（2026-11-10）

> 给接手 agent：从本文档开始，按「权威文档链」顺序读完即可无缝接手。
> 你的工作 = 续跑 V2 实现计划（31 任务，已完成 3 个，第 4 个进行中）。

## 0. 一句话状态

V1 已完整交付（master，已推 GitHub，主实验通过：探索臂 +16.7pp、零错误经验晋级）；
**V2（全深版）正在 `feat/v2-implementation` 分支执行，T1–T3 已完成提交，T4 处于 RED 已写、待实现状态。**

## 1. 权威文档链（按序读）

1. **本文件**
2. V2 设计文档（权威需求）：`docs/superpowers/specs/2026-11-10-evowancore-v2-design.md`
   ——含 §6 builder check 补答（北极星口径/预算计量/红线机制/评测门规则/MCP 验收方案）与 §9 M 清单，**不可跳过**
3. V2 实现计划（任务序列）：`docs/superpowers/plans/2026-11-10-evowancore-v2.md`
   ——31 任务 5 阶段（S1 地基 T1-9 / S2 闭环 T10-16 / S3 感知 T17-23 / S4 MCP T24-29 / S5 收尾 T30-31），每任务含 Files/Interfaces/RED 测试/提交信息
4. 执行台账：`.superpowers/sdd/2026-11-10-evowancore-v2/progress.md`（gitignored，任务完成记录与裁决）
5. Story 索引：`_bmad-output/stories/index.md`（gitignored，V2 Sprint 状态表）
6. 背景可选：V1 spec `docs/superpowers/specs/2026-11-09-evowancore-v1-design.md`、实验结论 `docs/experiments/RESULTS.md`

## 2. 仓库与环境事实

- 本地：`/Users/marcher/Desktop/EvoWanCore`；远程：`git@github.com:Marcher-lam/EvoWanCore.git`（SSH 已通）
- 当前分支：`feat/v2-implementation`（自 master `4e8c9f3` 分出）；master 干净
- 测试基线：`uv run pytest` = **160 passed**（T4 的 RED 测试会使全量显示 161 中 1 failed——这是预期的进行时状态）
- 质量门（每任务必须全过）：`uv run pytest` 全绿 + `uv run ruff check .` 无错（line-length 100，E501 不可自动修，提交前必须显式确认 ruff 干净）
- 模型：`.env`（gitignored）配置 DeepSeek（`api.deepseek.com/v1`，`deepseek-flash`，支持 tool calling + json_object，**不支持 json_schema**——适配器已有自动降级链）
- **key 曾在对话明文出现，用户已被告知建议轮换；不要把 key 写进任何被跟踪文件**
- 网络：PyPI 可用（uv 正常）；npm 不可用（ECONNRESET）——所以 MCP 参考验收用 Python SDK 自建 server（计划 T25）
- python 3.14（venv）、pytest-asyncio auto 模式、无 TodoWrite 工具（台账文件即进度）

## 3. 工作方法（用户指定：matt + superpowers + bmad）

- **superpowers**：按 `executing-plans` 流程逐任务执行——`task-start` 脚本取 brief → TDD 步骤 → 门禁 → `task-done` → 台账一行 → commit。中间文件：`/Users/marcher/.claude/plugins/cache/superpowers-marketplace/superpowers/7.0.1/skills/executing-plans/scripts/{task-start,task-done}`
- **TDD 铁律**：先写失败测试、亲眼看它失败、再实现、看它过。V1 期间两次先写实现后补 RED 被记录为瑕疵——不要重蹈
- **matt**：每任务接缝处测试先行、结束跑全量 + ruff
- **bmad**：每任务完成在 story 索引更新对应 Sprint 状态
- 执行模式：**native inline**（用户已确立，不派子代理实现）；最终评审（T31）才派最强模型子代理整分支评审
- 计划是契约：偏离必须记 `Ruling:` 进台账；冲突时 spec > plan > 你的判断

## 4. 精确接手点：T4（波次并发强制）进行中

**已完成**：RED 测试 `test_wave_parallelism_enforced` 已写入
`agent-explore/tests/test_exploration_controller.py`（文件末尾），**未提交**。
该测试用 `PeakVerifier`（verify_task 内计数+sleep）观测波内并发峰值，断言 `peak["max"] <= 2`（计划波 parallelism=2、4 任务）。当前 1 failed（无并发约束 → 峰值=4）。

**你要做的**：
1. 跑 `uv run pytest agent-explore/tests/test_exploration_controller.py::test_wave_parallelism_enforced -x` 确认 RED（失败原因应为峰值 4 > 2）
2. 实现：`agent-explore/src/agent_explore/controller.py` `_run_wave` 内用 `asyncio.Semaphore(plan 对应 parallelism)` 包每个任务协程——注意 `_run_wave` 目前不接收 wave 对象，parallelism 需从调用方（`_brs` 里 `plan.wave.parallelism`）传入，改签名时同步更新 `_drs`/测试调用点
3. GREEN → `uv run pytest` 全量 + ruff → 台账 + story 索引 → Commit `fix(M9a): wave parallelism enforced`
4. 继续 T5（预算真实计量，S1 最重要任务，是 T12/T16/T23 的前置）

## 5. V2 已完成任务（分支上的提交）

| 提交 | 任务 | 内容 |
|---|---|---|
| `fix(M1)` | T1 | environment_version 参数化出 Core；writer 盖戳；app 传 ENV_VERSION |
| `fix(M2,M3)` | T2 | discover `!=` 比较；空 refs lineage 回归（M3 实测 SQLite 容忍，仅留回归测试） |
| `fix(M7)` | T3 | verify 终态前重读 store，取消优先于过期内存态 |

## 6. 既定裁决与陷阱（浓缩自两本台账，避免重踩）

- **pydantic v2 静默忽略未知字段**：构造 ExplorationBudget(actions=4) 不会报错但字段被丢——字段名 `max_actions`（V1 实验曾因此预算无限）
- **aiosqlite `:memory:` 不可用**（每操作新连接）：测试一律 tmp_path 文件
- 测试假客户端必须 `async def`；monkeypatch `httpx.AsyncClient` 时先存 `orig` 防递归
- wave/task 的 id 在测试里固定（`"id": title`），便于 verifier 定向引爆/判分
- reflector 协议兼容同步/异步（controller 用 `inspect.isawaitable`）
- resume 测试必须等 checkpoint 落盘再模拟崩溃（V1 竞态根因）
- realtime 读循环用显式 `recv()`（对已关闭连接二次 `async for` 会无 await 空转冻死事件循环）
- StrEnum（不用 `(str, Enum)`）；`_execute_action` 的因果链事件 causation=proposed.id
- 记忆快照=波次边界语义（并行期间不写入）；诊断映射：failed 且 extract/export 前缀 → wrong_causal_model（T14 待做）

## 7. V2 北极星与红线（T16/T23 的判定标准）

- **北极星**：v3 感知化实验（难度 0.8、FakeVLM 读图、种子 41-43）——**explore 臂 extract 维度绝对值 ≥ 0.67**
- **红线**：错误经验晋级率 = 0（两轮 V1 实验均为 0，不得回退）
- 预算口径：真实模型调用计数（T5 实装），保留集任务**绝对禁止**进入评测门（T12 守卫）
- v3 前先跑 B 检查点实验（T16，文本语料，~10 分钟）

## 8. 收尾路径（T31 之后）

最终评审（Opus 子代理，携 Review Focus + Ruling 台账）→ Critical/Important 各自 RED→GREEN 修复一轮 → minors 记台账 → 合并 master → push。V1 已有完整先例（master 历史上 `fix: final-review fixes` 提交）。

## 9. 快速命令

```bash
uv run pytest                          # 全量测试
uv run ruff check . --fix              # lint（E501 手动拆行）
set -a; source .env; set +a            # 载入供应商配置
uv run python -m agent_devtools run-experiment --config <json> --out <json>   # 实验
git log --oneline master..HEAD         # V2 分支提交
```

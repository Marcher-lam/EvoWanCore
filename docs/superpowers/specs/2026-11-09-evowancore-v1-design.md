# EvoWanCore V1 设计文档

- 日期：2026-11-09
- 状态：待评审（builder check）→ 待用户确认
- 需求来源：`comunication-1.md`（RSIAgent 机理研究）、`comunication-2.md`（GEPA 机理研究）、`comunication-3.md`（产品架构 9 轮迭代）+ 2026-11-09 需求澄清对话
- 标注约定：**[已确认]** = 用户在澄清对话中明确拍板；**[我的裁量]** = 本文档代作的技术默认，用户未逐项确认，列于文末待复核

---

## 1. 意图与动机（Intent & Why）

EvoWanCore 是一个**全模态、可组合、可演化的 Agent 系统技术基座（内核 Core + SDK）**。

它不是某个业务 Agent，也不是又一个工作流框架。任务运行时、工具调用、多 Agent 编排、MCP/A2A 接入——这些现有框架（LangGraph、OpenAI Agents SDK 等）已经做了，而且会持续做得更好。EvoWanCore 的差异化全部押在一件事上：

> **自主探索 → 独立验证 → 记忆晋级 → 更强执行** 这个闭环成为内核的一等能力，让固定权重的模型通过积累可治理的环境经验，在同预算下完成更多任务。

三个理论来源各贡献一块：
- **RSIAgent**（comunication-1）：BRS 广度波次探索 + DRS 诊断驱动深练、Curriculum/Actor/Verifier 三角色分离、记忆防错（事实/假设/规则分级、按主张验证、反例出题、版本化回滚）
- **Voyager**：探索结果沉淀为可复用技能库（受治理的代码/流程资产，测试后发布）
- **GEPA**（comunication-2）：候选择优——小批次父子对比、实例级评估、评估通过才发布，防止"一次反思改坏整个系统"

**V1 的本质是一场风险退休**：整个项目最危险的假设是"RSIAgent 式练习在同预算下真的能带来可测量的提升"。V1 用最快速度端到端验证这个假设，同时把长期需要的运行时/记忆契约按最终形状定义好（只实现最小子集），避免日后推倒重来。

## 2. 目标 / 非目标 / 反目标

### 2.1 目标（V1 验收范围）

1. **探索/自进化闭环端到端可运行**：
   BRS 并行波次（共享记忆快照出发）→ DRS 诊断驱动深练 → CurriculumGenerator 出题（带验收条件与停止决策）→ Actor 执行 → **独立 Verifier 验收** → 候选记忆晋级（观察→主张→验证→限定适用范围→冲突检查→发布）→ Gap Ledger 记录缺口 → 新记忆驱动下一轮课程
2. **双模态一等支持**：图文理解（`infer()`）+ 实时音视频会话（`session()`：事件流、打断、背压、取消、会话恢复）
3. **契约稳定**：核心契约（见 §5.3）以 Pydantic v2 模型 + `typing.Protocol` SPI 固化，可导出 JSON Schema
4. **两个参考场景**：多模态文档 Agent（第一切片，探索测试床）、实时语音/Omni Agent（第二切片，流式运行时压测）
5. **可测量的验收指标**（见 §7）

**交付定位 [已确认 2026-11-09 第二轮]**：V1 是**内部验证原型**——按 SDK 的形状构建（包边界、契约、JSON Schema 导出全部照做），但不承诺对外稳定性、文档完备与 semver；对外发布是 V1 验证成功之后的事。

### 2.2 非目标（V1 明确不做）

- CUA 桌面/浏览器自动化的参考实现（EnvironmentAdapter 接口预留，不实现浏览器/桌面适配器）
- 组件级代码演化（DGM 式：候选修改 adapter/executor/composer 实现代码并构建——放 V2，其地基正是 V1 的发布治理轨道）
- 服务化部署形态与多语言 SDK（TS SDK、gRPC 服务边界——V1 是 Python 进程内 SDK；契约通过 JSON Schema 导出为将来留门）
- 低代码/可视化平台
- A2A 生产级互操作（V1 可预留 ProtocolAdapter SPI，不实现 A2A 适配器）**[我的裁量：MCP 工具接入是否进 V1 见 §5.4]**
- 自建 RTC 基座（实时会话 = 模型供应商 Realtime API 客户端）

### 2.3 反目标（做了就算失败）

- **错误经验污染长期记忆**：即使任务分上涨，若错误经验晋级率高企，V1 判定失败——可靠性优先于能力上限
- **执行者自证成功**：任何"Actor 说完成了就算完成"的路径进入主干
- **探索自降验收标准**：候选（经验/技能/策略）修改本轮评测或授权规则来证明自己有效
- **行业逻辑进 Core**：领域提示词、业务字段、页面选择器等出现在内核层
- **临时状态管理**：探索闭环用绕过契约的临时状态/无版本化记忆堆出来（文档第 5 轮警告的"流沙上盖探索"）

## 3. 约束

| 约束 | 内容 |
|---|---|
| 语言/运行时 | Python 3.11+，asyncio 原生（TaskGroup 管波次并行）；CPU 密集解析走进程池 |
| 契约技术 | Pydantic v2（数据契约，可导 JSON Schema）+ `typing.Protocol`（SPI 扩展点） |
| 代码组织 | monorepo 七包：`agent-core / agent-sdk / agent-explore / agent-evolve / agent-adapters / agent-devtools / examples` |
| 实时音视频 | 不自建 RTC：Realtime 会话以供应商 Realtime API 客户端形态进 Model Gateway（WebSocket 为主；WebRTC 场景用 aiortc 或外挂 LiveKit 独立服务）**[我的裁量]** |
| 扩展性铁律 | 第三方**不修改 Core** 即可：替换模型、增加工具/能力、实现自定义 Verifier/ExecutionPolicy/EnvironmentAdapter/Memory 组件、选择启用/关闭探索 |
| 三级模态声明 | 可接入（存储传递）≠ 可理解（有模型解析）≠ 可行动（有执行器操作）——能力是独立声明的，不得隐式升级 |

## 4. 方案选择（Approaches）

三个大分叉，均在澄清对话中拍板。

### 4.1 V1 主线：探索闭环先行（walking skeleton）**[已确认]**

- **候选 A：Runtime 平台优先**——先稳定 Runtime/Memory 全量契约与实现，探索闭环建在其上（comunication-3 第 5 轮的表面结论）
- **候选 B：探索闭环优先**——闭环作为第一个垂直切片端到端跑通，Runtime/Memory 契约按完整设计定义、只实现闭环拉出来的最小子集 ✅ **选定**
- **理由**：① 闭环是唯一无法从现有框架借来的部分；② 最大风险（练习是否真带来同预算提升）最先退休；③ 契约被真实用法拉出来，比预先空转设计更不容易错。第 5 轮"Runtime 优先"的真正关切（不在流沙上盖探索）通过"契约按完整形状定义、实现最小化"来满足，而非通过先建平台。

### 4.2 语言：Python **[已确认]**

- **候选 A：Python**——AI 生态最厚、进程内 SDK 对二次开发者原生开放 ✅ **选定**
- **候选 B：Go**——并发模型与 RTC 基建（pion/LiveKit）契合、单二进制部署干净、编译期契约；但二次开发者分布面收窄（进程内深度扩展只对写 Go 的人开放），交付被迫收敛为服务形态
- **候选 C：Python 内核 + Go 实时子服务**——增加跨语言运维复杂度，V1 不值得
- **理由**：用户预期第三方开发者主要活在 Python 生态，"import 一个库就深度定制"的形态是核心诉求；实时音视频需求通过供应商 Realtime API 客户端满足，回避了 Go 的最大优势项。

### 4.3 演化深度：停在经验/技能/提示层 **[已确认]**

- **候选 A：经验 + 技能 + 提示/策略候选**，统一"候选→评测→发布→回滚"轨道 ✅ **选定**
- **候选 B：含组件级代码演化**（DGM 式）——需构建管线、沙箱、安全审查一整套重基建
- **理由**：候选 A 覆盖三个理论来源的全部价值主张；候选 B 的地基（发布治理、回归、回滚）恰由 A 先建好。注意边界：**技能可以包含脚本/代码片段，但它们是受治理的资产（测试通过才发布、可回滚），不是"系统修改自己的实现代码"**。

### 4.4 测试床：多模态文档场景 **[已确认，场景组合]**

- 文档场景可重置、可程序化验收、练习成本低——正是探索需要的场地类型；CUA 推后、实时会话是经验消费者而非练习者。

## 5. 架构（The How，已定部分）

### 5.1 分层原则：机制与策略分离

```text
应用层      多模态文档 Agent / 实时语音 Omni Agent（examples）
              │
策略层      可替换：ExecutionPolicy / CurriculumGenerator / Verifier /
            MemoryWriter / Consolidator / Explor­ationPolicy / ModelAdapter /
            EnvironmentAdapter …（typing.Protocol SPI）
              │
稳定 Core   身份与权限 / 任务与 Run 状态机 / 调度与恢复 / 统一事件 /
            能力注册与执行网关 / 证据存证 / 记忆版本化 / 发布与回滚 /
            预算与审计
```

Core 管机制（状态机、事件、权限、版本），不管"Agent 怎么思考、怎么协作"——不强制 ReAct、不强制 Graph、探索是可选装配（`execute / explore / practice / evolve` 四模式）而非强制生命周期。

### 5.2 五条核心不变式

1. **执行/验证分离**：Verifier 是 Core 一等接口；验收证据来自环境状态/程序检查/业务证据，不来自 Actor 自述
2. **记忆分级**：每条记忆带 `epistemic_status`（观察/假设/已验证）+ 适用条件 + 环境版本 + 验证状态 × 生命周期双状态机，可修订、可撤销
3. **按主张验证**：任务整体 PASS 不为轨迹中每条结论背书——没检查的属性保持未知，不得总结成规则
4. **探索可选且不自降标准**：探索可以生成候选系统，不能修改生产实例、不能改本轮验收规则来证明候选有效
5. **事件模型能表达流**：RuntimeEvent 从第一天支持流式语义（供第二切片实时会话复用），即使第一切片只跑 batch

### 5.3 V1 落地契约清单

来源为 comunication-3 的接口设计（该文档含完整字段级骨架，本表只列 V1 范围裁决）：

**V1 实现（闭环 + 双场景所需最小集）：**

| 契约 | 说明 |
|---|---|
| `TaskSpec` / `TaskHandle` | 任务定义与实例：目标、输入输出 schema、约束、验收条件、预算 |
| `AgentSpec` | 身份、能力、执行策略引用 |
| `ActionRequest` / `ActionResult` | `mode: preview/execute/simulate/delegate`——"模型建议调用"与"系统实际执行"分离 |
| `ExecutionPolicy` | 单 Agent 如何推进（propose_next_actions / should_yield）——内置 1 个模型循环实现 |
| `CapabilityDescriptor` / `CapabilityRegistry` | 统一能力目录（tool / skill / model / retriever） |
| `ModelGateway`（`infer()` / `session()`） | 能力协商（ModelCapabilities：模态/交互/工具/约束）、流式会话 |
| `Verifier` | verify_action / verify_task；证据引用为入参 |
| `EvidenceStore` | write_observation / write_evidence / trace_lineage |
| `PolicyEngine` | authorize / enforce_budget / require_approval——副作用动作执行前实时鉴权 |
| `RuntimeEvent` | id/type/ts/subject/causation/correlation/payload；append-only |
| `Run` 生命周期 | CREATED→QUEUED→RUNNING↔SUSPENDED(结构化等待原因)→SUCCEEDED/FAILED/CANCELLED |
| `MemoryRecord` + 双状态机 | 验证态 CANDIDATE/VERIFIED/DISPUTED/REJECTED × 生命周期 ACTIVE/SUPERSEDED/EXPIRED/TOMBSTONED |
| `MemoryStore / Retriever / Writer / Consolidator / Publisher / ContextAssembler` | 六组记忆 SPI（V1 各给 1 个默认实现） |
| `ExplorationPolicy` | 探索对象选择、预算、停止条件 |
| `CurriculumGenerator` | plan_wave / refine_after_result / should_stop |
| `PracticeWave` / `PracticeTask` | 波次（共享记忆快照、并发度、合并策略）；任务族 prerequisite/variant/contrast/stress/target_attempt/regression |
| `AcceptanceSpec` | 每个练习任务自带验收规格（verifier_profile、必需证据、通过/失败条件） |
| `ExperienceCandidate` | 探索产物：观察→主张→验证→限定适用→冲突检查→发布 |
| `GapLedger` | 缺口对象（知识/技能/工具/感知/授权/验证/环境七类），带复现条件与关闭条件 |
| `ReleaseManifest` | 一次运行绑定的模型/能力/策略/记忆版本组合——可回放、可审计 |
| `EvolutionController`（最小版） | 候选→评测→发布→回滚轨道（V1 只承载经验/技能/提示候选） |

**V1 仅定义不实现（接口预留）：** `CompositionPolicy`（多 Agent 编排策略）、`ProtocolAdapter`（MCP/A2A 桥接）、CUA 类 `EnvironmentAdapter`、`AgentTransport`。

### 5.4 记忆子系统要点

- **四类状态分清**：运行状态（Runtime/StateStore）、认知记忆（Memory）、原始证据（Evidence/Artifact）、业务数据（DataConnector）——检查点不是记忆，向量索引不是记忆本体
- **命名空间**：`skills/* rules/* gaps/* coverage/* failures/* profiles/* releases/*`（comunication-3 第 8 轮）
- **晋级流水线**：原始轨迹（追加保存）→ 候选经验（验证通过才可晋级）→ 发布记忆（冲突检查+版本化后）；失败经验保守归纳（"某次脚本失败"不得升级为"该 API 不可用"）
- **冲突处理**：条件不同拆成两条条件规则；条件相同结果冲突→降可信度并触发 contrast 练习，不覆盖
- **课程视图**：CurriculumGenerator 读 `CurriculumMemoryView`（session 状态、能力前沿 mastered/shaky/missing/blocked、缺口、已验证规则、技能脆弱度、失败模式、覆盖图、开放问题），由 `CurriculumMemoryAssembler` 从多源投影——课程生成器站在"能力前沿"上出题，不站在"记忆堆"上回忆
- **删除语义**：逻辑失效（TOMBSTONED）与物理清除分开；原文/索引/缓存/派生摘要一致删除 **[我的裁量：V1 只做逻辑失效+全量保留，物理清除策略留 V2]**

### 5.5 数据与证据

- 四对象：`Asset`（原始媒体，哈希/来源/版本）/ `Segment`（时空切片：帧、音频区间、页面区域）/ `Observation`（环境观察）/ `Evidence`（主张←观察←派生过程）
- **可回到原始证据**：OCR/ASR/转写/摘要全部是带血缘的派生结果，绝不覆盖原始媒体
- 多模态记忆条目引用"原始媒体 + 派生表示 + 时空定位 + 来源关系"

### 5.6 参考场景（验收载体）

| 场景 | 切片 | 验证什么 |
|---|---|---|
| 多模态文档 Agent（图文） | 1 | 证据链与血缘、领域验收、**探索闭环在同预算下带来提升**（主验收场） |
| 实时语音/Omni Agent | 2 | session() 流式、打断、背压、工具调用、会话恢复；事件模型表达流；实时 Agent 作为已发布经验的消费者 |

### 5.7 测试床：合成文档工作台 [已确认 2026-11-09 第二轮]

主验收场（多模态文档 Agent）的具体环境，按自建合成工作台设计：

- **可种子化生成**：任务生成器按种子产出混合版式图文文档（报表、表单、扫描件、图表截图），难度维度可控（版式复杂度、字段密度、干扰项）
- **能力面**：一组文档工具（解析、字段抽取、格式转换、导出、校验），其中**刻意埋入可发现但非显然的环境约束**——导出参数依赖输入类型、字段校验规则的例外分支、格式转换陷阱、版本敏感行为。这些约束就是探索闭环要"学"的东西，对标 RSIAgent 练真实软件隐藏约束的设定
- **程序化验收**：任务生成时同步产出验收脚本（ground truth 来自生成器），Verifier 执行规则检查——验收全自动，不依赖人工评分
- **任务切分**：练习池 vs 保留集按能力维度分层切分；保留集任务在探索阶段不可见
- **记忆审计自动化**：晋级记忆的正确性对照生成器的已知真值 + 强模型复核，抽样 ≥30 条（或全量），无需人工逐条

## 6. 演化轨道（V1 边界内）

```text
探索发现（BRS 波次 / DRS 深练）
  → 独立验证（Verifier + 按主张验证）
  → 候选：经验条目 / 技能（含受治理脚本）/ 提示·策略候选
  → 评测：小批次父子对比（GEPA 式）+ 回归检查（新知识不得破坏旧能力）
  → 发布：ReleaseManifest 版本化；发布不直接改生产实例
  → 回滚：反例发现 → 降级/替代/失效（supersedes / invalidated_by 链传播）
```

四道防错门：主张分级（不允许自动越级）、独立验证、适用条件绑定（环境版本/权限/输入条件）、反例驱动（冲突优先安排验证而非继续复用）。

**技能脚本的执行隔离制度 [我的裁量，builder check 后补]**：候选技能中的脚本一律以**子进程**执行（超时/内存/输出体积受限，禁用网络按技能声明放行），**禁止进程内 eval/import 候选代码**；发布前提是契约测试通过（技能自带前置条件、预期输出、已知失效场景三要素）；已发布脚本作为版本化产物存储于技能库，回滚 = 切回旧版本产物。

## 7. V1 验收标准

**判定口径 [已确认 2026-11-09 第二轮：方向性标准]**——每配置以不同种子重复 ≥3 轮，报告均值与方差；学术级统计功效（power analysis）不在 V1 范围。

1. **探索收益（主指标）**：同预算对照实验——探索闭环消耗预算 W 后，在**未参与练习的保留任务集**上的表现 ≥ 把等量预算 W 用于直接多次重试的对照。通过线：探索组均值 ≥ 对照组均值，且任一能力分组的退步幅度不超过该组方差范围（超出方差才判退步）
2. **经验可靠性**：错误经验晋级率（§5.7 审计方案，首轮校准审计后设定固定阈值并写入实验报告）、反例发现率、版本回归率（更新后旧任务退化比例）
3. **扩展性测试（不改 Core）**：① 替换一个模型适配器；② 新增一个工具能力；③ 实现一个自定义 Verifier；④ 关闭探索运行同一应用
4. **运行可靠性**：中断恢复（检查点重放不重复副作用）、动作级 OUTCOME_UNKNOWN 处理（超时≠失败，不盲目重试）、发布与回滚演练
5. **第二切片**：实时会话 demo——语音打断后状态一致、会话恢复后记忆引用完整

## 8. 留给实现者的决策（builder 自由裁量区）

- 各契约的**字段级 schema**（以 comunication-3 的骨架为起点，允许微调命名与结构）
- 存储后端：V1 默认 SQLite + 本地文件系统（Asset blob store）即可，接口按可替换设计 **[我的裁量：默认 SQLite+文件]**
- 首批模型适配器：OpenAI 兼容协议作为基线适配器（覆盖多数供应商）+ 实测需要的具体供应商；VLM 与 Realtime 各至少一个可用实现 **[我的裁量]**
- 探索闭环默认参数：波次规模、并发度、预算单位（token/费用/动作数/时间）、停止阈值——做成可配置，默认值由实现者在校准实验中定
- BRS/DRS 的课程与反思提示词设计（属于 agent-explore 包的策略资产，不是 Core）
- 测试框架、CI、可观测性后端的选择
- 状态机迁移的实现形式（显式转移表 + 守卫函数，具体编码风格）

## 9. 风险与开放问题

| 风险 | 应对 |
|---|---|
| 验证器误判把错误经验晋级（RSIAgent 已暴露的失败模式） | 按主张验证 + 晋级门槛 + 审计抽样指标（§7.2）；这是主指标之一，不是附属 |
| 探索收益在文档场景上不显著 | 场景任务集设计需有难度梯度与可程序化验收；若 V1 内不显著，结论本身有价值（风险退休） |
| 实时切片挤压闭环工期 | 切片串行：闭环先行验收，实时切片不阻塞主指标 |
| 契约最小实现与完整形状漂移 | 契约测试（JSON Schema 快照）+ "仅定义不实现"清单受控 |
| asyncio 下的并行波次与记忆合并竞态 | 波次从共享只读快照出发，批完成后按序合并（RSIAgent BRS 同款语义） |

### 9.1 资源假设 [我的裁量，kickoff 时核实]

- **规模**：按单人~小团队、串行切片规划；第一切片（闭环）不依赖第二切片人力
- **供应商**：OpenAI 兼容基线适配器；假设 GLM/Z.ai 生态可用（含 VLM 与 Realtime 能力）。**实现计划的第一步是核实可用的 key、配额、实时模型与成本上限**——若 Realtime 供应商不可用，第二切片换供应商，不影响第一切片
- **预算**：实验设计受预算约束——小而精的保留集 + 缓存友好的任务生成（同种子同任务可缓存模型响应），重复轮数在成本内取下限

## 10. 待用户复核的裁量清单

| # | 裁量项 | 默认 |
|---|---|---|
| 1 | 实时音视频实现路径 | 供应商 Realtime API 客户端；WebRTC 需求用 aiortc/外挂 LiveKit |
| 2 | MCP 工具接入 | V1 不实现生产级 MCP 适配器，CapabilityDescriptor 预留映射（与 A2A 同等待遇）；若你认为 V1 必须能接 MCP 工具，请标注 |
| 3 | 记忆物理清除 | V1 只做逻辑失效+全量保留，物理清除/合规删除策略 V2 |
| 4 | 存储后端 | SQLite + 本地文件 |
| 5 | 模型适配基线 | OpenAI 兼容协议适配器为基线，VLM/Realtime 各至少一个实测实现 |
| 6 | 技能脚本执行隔离 | 子进程 + 资源限制 + 契约测试门禁发布；禁止进程内执行候选代码（见 §6） |

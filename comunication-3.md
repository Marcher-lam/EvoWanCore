<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# 和前文其他提及到的类似的方法实现一个具备rsiagent一样的具备探索机制的agent技术基座，从数据层面来说是支持全模态的数据，然后从模型层来说支持llm/vlm/omni类型，从任务角度来说，用户可基于此技术基座进行二次开发，然后实现用户的业务场景的适配，对于developer来说维护的是整个技基座的core，类似于sdk这类

你要做的应当是一套\*\*“具备自主探索与持续改进能力的全模态 Agent Core，并通过 SDK 向第三方开放”\*\*，而不是一个固定业务 Agent，也不是给现有工作流框架增加一个探索节点。**Core 维护通用运行机制，应用开发者提供领域环境、任务定义、能力扩展与验收规则；Agent 在这些契约下探索环境、积累经验，并适配具体业务。**

这与前文“通用 Core＋多模态 Harness＋能力注册＋自进化”的方向一致，但现在需要把**探索机制提升为内核的一等能力**，而不是某个应用独有的策略。[^1_1][^1_2][^1_3]

以下是我建议的产品与工程架构；接口名称、模块划分和流程均属于设计建议，不代表某个现有项目已经完整实现。

## 产品定位

建议使用这样的定义：

> 面向第三方开发者的全模态自适应 Agent 技术基座，提供统一数据与证据表示、异构模型接入、任务运行时、环境交互、自主探索、经验与技能演化，以及可扩展的开发接口。开发者无需修改 Core，即可构建领域 Agent，并选择启用探索、学习和自改进能力。

这里的“用户”需要分为三类，避免产品边界混乱：


| 角色 | 负责什么 | 不应被迫负责什么 |
| :-- | :-- | :-- |
| Core 维护者 | 运行时、协议、调度、探索框架、版本与安全机制、兼容性 | 为每个客户编写业务流程 |
| 应用开发者 | 数据源、领域工具、环境适配、任务规范、领域验证器、应用交互 | 修改 Core 才能增加一种场景 |
| 最终使用者 | 提交业务目标、提供必要信息、处理授权与异常 | 理解模型调用、探索策略和插件生命周期 |

**SDK 是交付入口，Core 才是产品本体。** SDK 可以同时支撑进程内嵌入、独立 Worker 和远程服务，不必先做成一个庞大的可视化平台。

## 总体架构

建议按照“稳定内核＋可插拔能力＋可版本化演化对象”组织：

```text
第三方业务应用
研究助手 / 多模态 RAG / CUA / 语音 Agent / 行业自动化
                         │
                  SDK / API / Events
                         │
┌──────────────────────────────────────────────────┐
│ Agent Core                                       │
│                                                  │
│ Task Runtime       任务状态、调度、恢复、协作      │
│ Exploration        课程、探索策略、缺口、停止条件  │
│ Experience         证据、记忆、技能、适用边界      │
│ Evolution          候选构建、评测、发布、回滚      │
│ Capability         工具、Skill、MCP、Agent 能力    │
│ Model Gateway      能力协商、路由、流式会话        │
│ Data & Evidence    全模态资产、对齐、血缘、索引    │
│ Governance         权限、预算、隔离、审计          │
└──────────────────────────────────────────────────┘
                         │
                  Adapter / Plugin SPI
                         │
模型供应商 / 数据系统 / 浏览器 / 桌面 / 沙箱 / 业务 API
```

这里有一个重要区别：

- **应用层决定做什么、怎样才算业务成功。**
- **Core 决定任务如何运行、探索如何组织、经验如何晋级。**
- **适配器决定如何与具体模型、数据源和环境交互。**

不要把行业提示词、数据库字段、页面选择器写进 Core；也不要把恢复、审计、权限等底层责任全部推给业务插件。

## 数据层：统一而不抹平

“全模态”不应理解为“所有输入最后都转成文本”，而应理解为：**共享资产、事件和证据协议，同时保留模态原生结构。**

建议至少划分四类数据对象：


| 对象 | 表达内容 | 关键字段 |
| :-- | :-- | :-- |
| `Asset` | 图片、音频、视频、文档、结构化数据、其他二进制资产 | 类型、存储引用、哈希、权限、来源、版本 |
| `Segment` | 文本片段、页区域、视频帧、音频区间、对象区域 | 父资产、时间范围、空间坐标、通道 |
| `Observation` | 某次环境观察 | 时间、环境版本、相关资产、状态摘要 |
| `Evidence` | 支持某项判断的证据 | 主张、原始观察、派生过程、验证状态 |

例如，一段会议视频不应只有“转写文本”，还应保留：

```text
原始视频
  → 音轨
  → 带时间戳的转写
  → 关键帧与屏幕区域
  → 说话人与画面事件的对应关系
  → 业务判断引用的具体片段
```

核心要求是**可回到原始证据**：OCR、ASR、摘要和模型判断都应作为派生结果保存，不能覆盖原始媒体。

此外，应分别声明三种支持范围：

1. **可接入**：系统可以存储和传递该类型。
2. **可理解**：已有模型或处理器能够解析。
3. **可行动**：已有执行器能够基于它操作环境。

支持视频上传，不等于支持实时视频交互；能看截图，也不等于具备可靠的桌面操作能力。

## 模型层：按能力协商

LLM、VLM、Omni 可以作为便于理解的类别，但运行时不要只根据类别名称分配任务。

建议建立 `ModelCapabilities`：

```text
输入：text / image / audio / video / structured
输出：text / image / audio / structured
交互：batch / streaming / realtime
工具：native_tool_call / structured_action
约束：上下文、分辨率、采样率、并发、地域、预算
```

例如，“音频输入”和“音频输出”同时存在，也不能直接推断模型支持双向实时会话；这些应是独立的能力声明。

模型层需要提供两种接口，而不是一个万能 `generate()`：


| 接口 | 面向场景 | 运行时重点 |
| :-- | :-- | :-- |
| `infer()` | 文本推理、图像理解、批量处理 | 请求响应、结构化输出、工具调用 |
| `session()` | 实时语音、持续视频、交互式 Omni | 事件流、打断、背压、取消、会话恢复 |

**模型替换还应触发经验兼容检查。** 某项技能如果依赖坐标输出、特定工具调用格式或特定视觉能力，就不能仅因为换了一个“更强模型”便默认继续有效。

## 探索是独立运行模式

沿用前文对 RSIAgent 的讨论，可以借鉴 BRS 的广泛探索与 DRS 的针对性深练，但应将二者实现为可替换策略，而不是所有业务必须执行的固定流程。[^1_4]

Voyager 的自动课程、反馈与技能库，也适合作为探索子系统的参考；其实现面向 Minecraft，因此不能直接视为通用业务 Core。[^1_5]

建议提供四种模式：


| 模式 | 目的 | 是否允许更新能力 |
| :-- | :-- | :-- |
| `execute` | 使用已发布能力完成业务任务 | 默认不直接发布更新 |
| `explore` | 主动认识环境、发现未知行为 | 生成候选经验 |
| `practice` | 针对明确薄弱点重复验证 | 生成技能或策略候选 |
| `evolve` | 修改技能、配置或代码 | 经评测发布新版本 |

这四种模式共享运行时与证据系统，但应使用不同的权限、预算和环境。

### 探索闭环

```text
目标与环境描述
      ↓
能力盘点与未知项识别
      ↓
提出探索问题
      ↓
生成带验收条件的练习
      ↓
沙箱或测试环境执行
      ↓
独立检查环境变化
      ↓
形成候选经验与能力缺口
      ↓
反例、冲突与回归检查
      ↓
发布经验或技能版本
      ↓
更新下一轮探索计划
```

**探索任务本身必须有验收条件。**

“了解这个软件”太宽泛；“确认导出操作是否保留原始时间戳，并用不同样本复验”才是可执行的探索任务。

### 探索策略接口

Core 应内置策略框架，允许应用配置或扩展：

- 广度探索：覆盖不同功能、状态和操作类型。
- 缺口驱动：针对失败、未知约束和低置信度能力。
- 反例探索：主动寻找已有经验失效的条件。
- 目标驱动：只探索与当前业务目标有关的能力。
- 开放探索：在预算与边界内持续发现新能力。

预算不应只有 Token，还应包括时间、模型费用、环境操作次数、并发数和副作用额度。

停止条件也应明确：达到覆盖目标、收益趋于停滞、预算耗尽、风险超限或缺少必要授权，都可以终止探索。

## 将能力缺口结构化

建议把 `Gap Ledger` 作为 Core 的正式对象，而不是一段失败日志。


| 缺口类型 | 示例 | 默认处理方向 |
| :-- | :-- | :-- |
| 知识缺口 | 不知道某操作的前置条件 | 探索、查阅、验证 |
| 技能缺口 | 已有工具但不会组合调用 | 练习或合成 Skill |
| 工具缺口 | 缺少格式转换器 | 生成适配器或请求安装 |
| 感知缺口 | 无法解析某种媒体 | 接入处理器或切换模型 |
| 授权缺口 | 无权访问业务系统 | 请求授权，不尝试绕过 |
| 验证缺口 | 无法判断业务是否成功 | 请求规则、测试数据或人工确认 |
| 环境缺口 | 缺少测试账号、模拟器 | 请求补齐环境 |

**“不知道怎么做”和“没有权力做”必须分开。** 同样，“没有验证证据”不能被自动归类为“执行成功”。

每个缺口应保存复现条件、相关证据、影响范围、已尝试方案、处理负责人和关闭条件，方便业务开发者接管。

## 经验与技能分开管理

建议不要用一个向量库承载全部“记忆”。


| 层级 | 保存什么 | 更新原则 |
| :-- | :-- | :-- |
| 运行上下文 | 当前任务状态与临时信息 | 随任务生命周期管理 |
| 原始轨迹 | 操作、结果、环境观察 | 追加保存，敏感信息受控 |
| 候选经验 | 尚待验证的观察与假设 | 不直接作为稳定规则 |
| 已验证知识 | 有证据、适用条件的规则 | 可修订、可撤销 |
| 可执行 Skill | 步骤、代码、依赖、权限 | 测试后版本化发布 |
| 应用发布包 | 模型、插件、技能与策略组合 | 固定版本、支持回滚 |

前文已经讨论过：避免错误积累，重点是区分事实、推测和可复用规则，并保留证据与撤销能力。[^1_6]

因此，候选经验的晋级建议至少经过：

```text
观察 → 主张 → 验证 → 限定适用范围 → 冲突检查 → 发布
```

验证器与执行器分离，不代表必须使用不同厂商模型；更重要的是，验证结果不能只来自执行器自己的“我完成了”，而应尽量来自环境状态、程序检查或业务证据。

## 自进化与 Core 维护

这里需要同时满足两个目标：**开发者维护稳定 Core，Agent 又可以探索并改进自身软件栈。**

建议将“能修改什么”与“谁有权发布”分开：


| 对象 | 建议的演化方式 | 发布边界 |
| :-- | :-- | :-- |
| 领域经验、Skill | 自动生成与修订候选 | 应用级策略控制 |
| 提示、规划、检索与路由策略 | 候选搜索与评测 | 应用发布包控制 |
| 工具和模型适配器 | 隔离构建、契约测试 | 插件版本控制 |
| Runtime 或 Core 代码 | 在独立分支构建候选版本 | Core 发布流程控制 |
| 本轮评测与授权规则 | 可提出未来修订建议 | 不得由候选自行更改后据此证明通过 |

GEPA 可以作为“利用执行反馈改进提示、代码或配置”的方法参考，但它不是完整的全模态运行时或发布治理系统。[^1_7][^1_8]

因此，你不必把自进化限制为“只改提示词”，也不需要允许 Agent 直接覆盖正在运行的生产 Core：

```text
当前稳定版本
      ↓
生成修改候选
      ↓
独立构建与隔离预演
      ↓
契约测试、回归、安全与业务评测
      ↓
灰度发布
      ↓
持续观察或回滚
```

这样，Agent 可以贡献 Core 改进，但 Core 维护者仍然掌握兼容性和正式版本。

## 二次开发契约

**真正决定它是否是技术基座的，不是支持多少 Demo，而是新场景能否在不修改 Core 的情况下接入。**

建议优先稳定以下扩展接口：


| 接口 | 应用开发者提供 |
| :-- | :-- |
| `DataConnector` | 数据获取、更新、删除和权限映射 |
| `ModelAdapter` | 模型能力声明、推理或实时会话 |
| `EnvironmentAdapter` | 观察、行动、状态检查、重置能力声明 |
| `CapabilityProvider` | 本地函数、API、MCP 或其他能力 |
| `TaskSpec` | 输入输出、业务目标、约束与完成条件 |
| `Verifier` | 领域验收与证据检查 |
| `ExplorationPolicy` | 探索对象选择、课程与停止策略 |
| `AgentTransport` | Agent 间通信与自定义协议 |

其中 `EnvironmentAdapter` 很关键：API、浏览器、桌面与模拟器应进入同一交互框架，但**不要求它们都能快照或回滚**。不支持恢复的环境，应明确声明，并限制探索动作。

下面是 SDK 使用体验的示意，不是现成可运行接口：

```python
core = AgentCore(
    models=model_registry,
    capabilities=capability_registry,
    storage=storage,
    policy=runtime_policy,
)

app = core.application(
    name="domain_agent",
    environment=BusinessEnvironment(),
    task_spec=BusinessTaskSpec(),
    verifier=BusinessVerifier(),
    exploration=ExplorationProfile(
        strategies=["breadth", "gap_driven", "counterexample"],
        budget=exploration_budget,
        publish_policy="evaluated",
    ),
)

await app.explore(scope="test_workspace")
result = await app.run(task, release="stable")
```

开发者应主要配置领域组件，而不是重新实现探索循环、任务恢复或技能版本管理。

## 交付与验收

建议首版交付采用 **Core＋SDK＋插件契约＋参考应用**，而不是先投入大量精力建设低代码界面。

可以按以下包边界组织：

```text
agent-core       契约、状态机、事件、运行机制
agent-sdk        开发者入口与应用封装
agent-explore    课程、探索、缺口管理
agent-evolve     候选、评测、发布、回滚
agent-adapters   模型、环境、数据与协议适配
agent-devtools   CLI、轨迹查看、契约测试
examples         参考应用
```

首版验收建议选三类差异明显的参考场景，而不是做三个相似聊天助手：


| 参考场景 | 验证什么 |
| :-- | :-- |
| 多模态文档 Agent | 原始媒体、派生结果、证据引用与领域验收 |
| 测试环境 CUA Agent | 观察—行动—验证、环境探索与技能积累 |
| 实时语音或 Omni Agent | 流式事件、打断、工具调用与会话状态 |

同时设置四类硬指标：

- **可扩展性**：新增模型、环境或业务场景是否需要改 Core。
- **探索收益**：同预算下，探索后在未参与练习的任务上是否更好。
- **经验可靠性**：错误经验晋级率、反例发现率、版本回归率。
- **运行可靠性**：中断恢复、重复副作用防护、权限隔离与回滚表现。

下一步最值得先写的不是更多功能清单，而是 **`TaskSpec`、`EnvironmentAdapter`、`Evidence`、`Capability`、`ExperienceCandidate`、`ReleaseManifest` 六份核心契约**。它们分别确定“做什么、在哪里做、凭什么判断、能够做什么、学到了什么、运行哪个版本”，也是探索型 Agent 从研究流程变成可维护 SDK 产品的工程主线。

<span style="display:none">[^1_10][^1_11][^1_12][^1_13][^1_14][^1_15][^1_16][^1_17][^1_18][^1_19][^1_20][^1_9]</span>

<div align="center">⁂</div>

[^1_1]: https://www.perplexity.ai/search/f00a0b6a-8809-42e0-a9ee-93d8a44b1cc8

[^1_2]: https://www.perplexity.ai/search/2b867baa-e690-4869-9988-0546ec0a68d1

[^1_3]: https://www.perplexity.ai/search/8f47bd07-22fa-4e7b-88c8-06c8c4be7ed1

[^1_4]: https://www.perplexity.ai/search/f58ac2ce-fad6-4c2c-8cf8-ef863d6b8e25

[^1_5]: https://github.com/MineDojo/Voyager/tree/main

[^1_6]: https://www.perplexity.ai/search/6cf95d04-5191-43a5-9877-1fb52cb42a3d

[^1_7]: https://github.com/rartzi/gepa

[^1_8]: https://gepa-ai.github.io/gepa/guides/faq/

[^1_9]: https://github.com/tangle-network/gepa-ts

[^1_10]: https://github.com/benchflow-ai/gepa

[^1_11]: https://github.com/CerebrasResearch/gepa

[^1_12]: https://neurips.cc/virtual/2023/77597

[^1_13]: https://github.com/xiumaoprompt/gepa-ui

[^1_14]: https://arxiv.org/html/2607.07663v2

[^1_15]: https://towardsdatascience.com/tool-use-agents-and-the-voyager-paper-5a0e548f8b38/

[^1_16]: https://gepa-ai.github.io/gepa/guides/

[^1_17]: https://github.com/Yunicorn228/GEPA_2026s

[^1_18]: https://gepa-ai.github.io/gepa/

[^1_19]: https://arxiv.org/abs/2507.19457

[^1_20]: https://www.reddit.com/r/MachineLearning/comments/1wgazy4/rsi_is_not_happening_r/


---

# 系统支持MCP/A2A/FC/tooluse以及多agent编排，本质上是一个支持任意agent系统形态设计的技术基座

对，这意味着需要把定位再明确一层：**你要建设的是“Agent 系统技术基座”，而不仅是“能够开发某类 Agent 的框架”。** MCP、A2A、Function Calling、Tool Use、多 Agent 编排、全模态与自主探索，都应成为这个基座中可组合、可替换的能力，而不是预先限定应用形态。

**核心原则是：统一底层运行契约，但不统一上层 Agent 的组织方式。** 开发者既可以构建简单单 Agent，也可以构建工作流、多 Agent 团队、分布式协作网络，以及具备探索和自进化机制的系统。

以下是对上一版架构的进一步修正与收敛，属于建议设计。

## 产品定义

建议将正式定位写成：

> **面向第三方开发者的全模态、可组合、可演化 Agent 系统内核与 SDK。**\
> 提供统一的数据、模型、能力、通信、执行、编排与演化契约，支持开发者构建不同组织形态的 Agent 系统，并接入外部 Agent、工具、协议和运行环境。

这里最重要的变化是：**Agent 不再是 Core 强制规定的一种固定循环，而是可以由不同执行策略与协作结构组成的运行实体。**

不过，“支持任意形态”应作为架构目标，而不是字面上的无限兼容承诺。更准确的工程表达是：

> 不强制单一规划方式、通信协议、协作拓扑或执行模式；新的系统形态可通过公开扩展接口实现，而无需修改内核。

## 区分不同抽象层

MCP、A2A、FC、Tool Use 和多 Agent 编排不是同一层面的概念，不能简单并列成五种“协议”。

MCP 提供工具、资源、提示等接入原语；A2A 定义 Agent 间的任务、消息与产物交互；Function Calling 则涉及模型生成调用请求、应用执行并返回结果的过程。[^2_1][^2_2][^2_3]

建议按下面的职责组织：


| 能力 | 所处层面 | 在基座中的设计职责 |
| :-- | :-- | :-- |
| MCP | 外部能力与上下文接入 | 客户端连接、能力发现、资源读取、工具映射；按需提供服务端暴露 |
| A2A | Agent 互操作 | 发现远端 Agent、委派任务、跟踪状态、交换产物 |
| Function Calling | 模型输出与调用绑定 | 将供应商返回的调用请求转换为内部动作 |
| Tool Use | 能力使用与执行机制 | 解析、校验、授权、执行、反馈与验证 |
| 多 Agent 编排 | 系统组织与调度 | 定义角色、依赖、协作方式、共享状态与终止条件 |
| 自定义协议 | 扩展边界 | 映射外部语义，保留无法通用化的协议特性 |

在这个设计中，**模型请求调用工具，不等于工具已经执行；Agent 之间可以通信，也不等于已经形成可靠的协作系统。**

A2A 提供任务生命周期与消息、产物等交互对象，但你的业务调度、协作策略和任务验收仍需要由基座或应用实现。[^2_1]

## 支持哪些系统形态

建议把“系统形态”设计成多个可独立选择的维度，而不是给开发者一个固定的 `AgentType` 枚举。


| 维度 | 可支持的设计选择 |
| :-- | :-- |
| 决策方式 | 确定性规则、模型决策、规划执行、反思修正、混合决策 |
| 组织结构 | 单体、主管—执行者、层级团队、对等协作、黑板式协作 |
| 调度方式 | 顺序、并行、条件分支、事件驱动、动态派生 |
| 生命周期 | 单次调用、会话、长任务、常驻服务 |
| 部署方式 | 进程内、多进程、远程服务、跨组织协作 |
| 交互方式 | 文本、图像、音频、视频、实时流与混合输入输出 |
| 学习方式 | 固定能力、检索记忆、探索练习、技能演化、代码候选演化 |
| 人机关系 | 全自动、审批、协作编辑、人工接管 |

例如，一个应用可以同时是：

> 实时语音入口＋主管 Agent＋多个远端专业 Agent＋MCP 工具＋事件驱动执行＋后台探索练习。

Core 不应该因为开发者选择了“多 Agent”，就强制所有成员使用相同模型、相同记忆或相同规划循环。

## 内核应提供什么

上一版架构需要进一步补齐 **Agent 实体、通信和编排的显式契约**。建议形成以下几个核心子系统：

```text
第三方 Agent 系统
单 Agent / 工作流 / 多 Agent 团队 / 分布式协作 / 探索型系统
                              │
                         SDK / API
                              │
┌─────────────────────────────────────────────────┐
│ Agent System Core                               │
│                                                 │
│ Agent Runtime      实体、生命周期、状态与恢复    │
│ Composition        组合、调度、依赖与协作策略    │
│ Communication      消息、委派、产物与协议桥接    │
│ Capability         工具、Skill、MCP、能力发现   │
│ Model Gateway      LLM / VLM / Omni 适配         │
│ Data & Evidence    全模态数据、证据与血缘        │
│ Exploration        课程、练习、能力缺口          │
│ Evolution          候选、评测、发布与回滚        │
│ Governance         身份、权限、预算与审计        │
└─────────────────────────────────────────────────┘
                              │
模型 / 数据源 / MCP Server / 外部 Agent / 执行环境
```

其中，**探索与演化是可装配的子系统，不应成为所有 Agent 的强制生命周期。** 一个确定性业务 Agent 应当可以完全关闭探索；一个探索型系统则可以把探索作为主工作负载。

## 最小运行原语

要避免“每加入一种 Agent 范式就改一次 Core”，建议内核围绕相对稳定的原语设计，而不是围绕某种热门提示方法设计。


| 原语 | 含义 | 需要明确的契约 |
| :-- | :-- | :-- |
| `Agent` | 可执行、可寻址的运行实体 | 身份、能力、生命周期、执行策略 |
| `Task` | 具有目标与状态的工作单元 | 输入、约束、完成条件、取消与超时 |
| `Message` | 实体间传递的信息 | 来源、目标、关联任务、内容与权限 |
| `Action` | 一次拟执行的操作 | 参数、目标、风险、授权与结果 |
| `Artifact` | 任务形成的产物 | 类型、版本、来源与访问控制 |
| `Event` | 已发生的运行事实 | 顺序、关联关系、持久化与订阅 |
| `Capability` | 可以被发现和使用的能力 | 描述、输入输出、权限与版本 |
| `ExecutionPolicy` | 如何推进一个 Agent | 规则、模型循环或其他自定义逻辑 |

**“统一”应统一身份、追踪、权限和生命周期，不应抹平不同对象的语义。**

例如，远程 Agent 可以被注册为可发现能力，但调用它时仍应保留任务状态、补充输入、取消和产物获取等行为，而不是一律降级成同步 `tool()`。

## 编排必须可替换

建议把编排分成两层：

\*\*机制层由 Core 维护：\*\*任务启动、调度、等待、取消、超时、状态持久化、事件传递和故障恢复。

\*\*策略层允许插件化：\*\*谁做决策、如何分工、何时并行、是否投票、是否使用共享黑板，以及什么时候停止。

因此，可以提供这些编排器，但不要求应用只能选择其中之一：

- `SequentialComposer`：顺序执行。
- `ParallelComposer`：并行处理与结果汇聚。
- `SupervisorComposer`：主管动态委派。
- `EventDrivenComposer`：根据事件持续推进任务。
- `BlackboardComposer`：围绕受控共享状态协作。
- `CustomComposer`：开发者实现自己的组织方式。

**不必强制 Graph，也不应排斥 Graph。** 图可以是上层编排器的表达形式，但不应成为整个内核唯一的执行模型。

同样，要区分持久化事件记录与业务重新执行：恢复状态可以重放日志，但发邮件、付款等外部副作用不能因为重放而再次触发。

## 通信与上下文治理

多 Agent 系统不应默认共享完整历史、全部工具权限和所有凭据。

建议每次委派明确传递：

```text
任务目标与验收条件
允许使用的能力
必要的上下文和证据引用
预算与截止时间
可返回的产物类型
调用方身份与受限授权
```

并明确区分三种操作：

- **消息发送**：传递信息，不一定产生新任务。
- **任务委派**：产生可跟踪、有完成条件的工作单元。
- **控制权交接**：改变当前交互或执行的负责实体。

对于自定义协议，适配器需要声明支持哪些语义。无法映射的能力应明确报错或暴露为扩展，不能静默假装兼容。

## 探索也支持组合

既然面向任意 Agent 系统形态，探索也不应只能发生在“单 Agent 内部”。

建议支持三个层级：


| 探索层级 | 探索对象 | 可能形成的改进 |
| :-- | :-- | :-- |
| 单 Agent | 工具使用、环境规律、操作流程 | 经验、Skill、规划策略 |
| 团队 | 角色分配、交接方式、消息内容 | 协作规则、上下文分配、委派策略 |
| 系统 | 组件组合、模型选择、运行结构 | 路由、拓扑、配置或代码候选 |

例如，系统发现业务失败并非源于某个 Agent 不会操作，而是主管遗漏了关键证据。此时应改进委派与上下文策略，而不是不断给执行 Agent 增加提示词。

这里仍应保持边界：**探索可以生成新的系统结构候选，但不能自行降低验收标准来证明候选有效。** 候选拓扑也需要独立评测、版本固定和回滚。

## SDK 的最终形态

建议对外同时提供三个入口：


| 入口 | 面向开发者 | 使用方式 |
| :-- | :-- | :-- |
| 高层 SDK | 快速开发业务系统 | 使用内置 Agent、编排器和探索配置 |
| 低层 SDK / SPI | 自定义 Agent 范式 | 实现执行策略、编排器、协议与适配器 |
| 服务 API | 跨语言和远程集成 | 提交任务、订阅事件、连接外部 Agent |

还应支持**外部 Agent 托管式接入**：第三方已有 Agent 不必重写内部循环，只需通过协议或适配器接入。对于这种黑盒 Agent，Core 应明确可观测与可控制边界，不能假定自己拥有其内部轨迹或回滚能力。

对应到项目的核心验收标准，就是：

> **不修改 Core，能否替换模型、增加工具、接入外部 Agent、改变协作结构、定义新的执行循环，并选择性启用探索与演化？**

下一版架构最需要补充的不是更多功能名称，而是 **`AgentSpec`、`ExecutionPolicy`、`CompositionPolicy`、`MessageEnvelope`、`DelegationContract` 和 `ProtocolAdapter`** 六份契约。它们与上一版的数据、证据、任务和演化契约结合，才能真正支撑你所说的“任意 Agent 系统形态设计”。

<span style="display:none">[^2_10][^2_11][^2_12][^2_13][^2_14][^2_15][^2_4][^2_5][^2_6][^2_7][^2_8][^2_9]</span>

<div align="center">⁂</div>

[^2_1]: https://a2a-protocol.org/latest/topics/key-concepts/

[^2_2]: https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture

[^2_3]: https://developers.openai.com/api/docs/guides/function-calling?api-mode=responses

[^2_4]: https://www.ibm.com/think/topics/agent2agent-protocol

[^2_5]: https://a2a-protocol.org/pr-849/specification/

[^2_6]: https://modelcontextprotocol.io/specification/2026-07-28/architecture

[^2_7]: https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/

[^2_8]: https://dev.to/aws-builders/mcp-explained-how-the-model-context-protocol-transforms-ai-in-the-cloud-4hba

[^2_9]: https://codelabs.developers.google.com/intro-a2a-purchasing-concierge

[^2_10]: https://developers.openai.com/api/docs/guides/tools

[^2_11]: https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api

[^2_12]: https://www.analyticsinsight.net/openai/openai-function-calling-how-it-works-with-practical-examples

[^2_13]: https://eseospace.com/blog/mcp-resources-explained/

[^2_14]: https://prismix.dev/guides/openai-function-calling-guide

[^2_15]: https://deepwiki.com/openai/openai-cookbook/6-function-calling-and-tool-integration


---

# 系统支持MCP/A2A/FC/tooluse以及多agent编排，本质上是一个支持任意agent系统形态设计的技术基座

**是的，项目应正式定位为“支持不同 Agent 系统形态设计、运行与演化的通用技术基座”，而不是某种固定 Agent 架构。** MCP、A2A、FC、Tool Use、多 Agent 编排是它提供的基础能力；全模态、模型适配、自主探索与自进化则横向贯穿这些能力。

上一版还需要进一步强调：**不仅业务内容可定制，Agent 的决策机制、组织结构、通信方式和运行生命周期也必须可定制。** 以下按这个定位固定设计边界，不再把它收缩为单 Agent SDK 或多 Agent 工作流框架。

## 产品的正式定义

建议采用：

> **一个面向第三方开发者的全模态 Agent 系统内核与 SDK，提供模型接入、能力管理、协议互操作、任务运行、多 Agent 编排、探索学习与受控演化机制，允许开发者通过组合配置和扩展接口，构建不同业务场景、组织结构与执行范式的 Agent 系统。**

“支持任意形态”的工程含义不是预置所有形态，而是：

- 不强制一种 Agent 循环。
- 不强制一种编排拓扑。
- 不强制一种通信协议。
- 不绑定某个模型供应商或模态。
- 不要求所有 Agent 都由 Core 创建。
- 不要求所有应用启用探索或自进化。
- 新形态能够通过公开接口实现，而不是修改内核。

**产品提供通用机制与参考实现，开发者决定系统形态。**

## 协议不是系统架构

MCP 主要提供工具、资源和提示等接入原语；A2A 则提供 Agent 发现，以及任务、消息和产物等互操作对象。[^3_1][^3_2]

因此，建议明确区分以下职责，而不是将它们全部包装成一种工具调用：


| 能力 | 基座中的职责 | 不应混淆为 |
| :-- | :-- | :-- |
| MCP | 接入、发现和暴露工具与上下文资源 | 完整的 Agent 编排机制 |
| A2A | 接入外部 Agent、委派任务、交换状态和产物 | 本地函数调用 |
| FC | 将模型生成的结构化调用请求映射为内部动作 | 已经完成动作执行 |
| Tool Use | 管理能力选择、授权、执行、反馈与验证 | 某一家模型的返回格式 |
| 多 Agent 编排 | 管理角色、依赖、协作、调度与结果汇聚 | 单纯消息转发 |
| 自定义协议 | 允许领域系统保留自己的交互语义 | 无限制、无损的万能协议转换 |

特别是远程 Agent，应该保留长任务、补充输入、取消与产物等语义，不能一律压缩成同步的 `call_tool()`。A2A 本身就将任务建模为具有生命周期的有状态工作单元。[^3_3]

## 系统形态如何开放

建议不要用一个固定的 `AgentType` 枚举描述整个系统，而是让多个维度独立组合：


| 维度 | 开发者可选择的形态 |
| :-- | :-- |
| 决策机制 | 规则驱动、模型循环、规划执行、反思修正、自定义策略 |
| 协作结构 | 单 Agent、主管—执行者、层级团队、对等协作、共享黑板 |
| 调度机制 | 顺序、并行、图式、事件驱动、动态派生 |
| 生命周期 | 单次任务、会话、长期任务、常驻服务 |
| 部署结构 | 进程内、多进程、分布式、跨组织 |
| 交互模态 | 文本、图像、音频、视频、实时多模态 |
| 改进方式 | 固定能力、经验积累、主动探索、Skill 演化、代码候选演化 |
| 人工介入 | 全自动、审批、协作、接管 |

例如，开发者可以构建“实时语音入口＋主管 Agent＋远程专业 Agent＋MCP 工具＋后台探索”，也可以只使用同一 Core 做一个固定流程的文档处理系统。

**简单系统不应该被迫承担复杂系统的全部依赖；复杂系统也不应该被简单接口限制。**

## Core 的稳定边界

建议把架构划分为三层：

```text
应用与系统形态
业务 Agent / 多 Agent 团队 / CUA / RAG / 实时交互 / 探索系统
                           │
可替换策略与扩展
执行策略 / 编排策略 / 通信适配 / 探索策略 / 领域验证器
                           │
稳定 Core
身份与权限 / 任务状态 / 调度与恢复 / 事件与证据
能力注册 / 数据与模型契约 / 版本与发布
```

这里最重要的是**机制与策略分离**：


| Core 维护的机制 | 开发者可替换的策略 |
| :-- | :-- |
| 任务状态与生命周期 | 如何规划、分解和执行任务 |
| 调度、等待、取消、超时 | 谁先运行、如何并行、何时交接 |
| 消息投递与关联追踪 | 采用主管、对等还是黑板协作 |
| 能力注册与执行网关 | 选择哪些工具、Skill 和 Agent |
| 证据记录与验证接口 | 怎样判断领域业务是否成功 |
| 候选版本与发布流程 | 探索什么、改进什么、如何生成候选 |

因此，**Graph 可以支持，但不是内核唯一范式；ReAct 可以内置，但不是所有 Agent 必须继承的基类。**

## 两级编程接口

要真正支持不同系统形态，SDK 不能只有高层的 `Agent(...).run()`，建议同时提供两级接口。

**高层组合接口**面向应用开发者：使用预置 Agent、编排器、模型适配和探索配置，快速实现业务。

**低层扩展接口**面向系统开发者：替换执行循环、协作方式、通信协议和运行适配，不必绕过 Core 的权限与审计机制。

建议优先稳定这些契约：


| 契约 | 定义内容 |
| :-- | :-- |
| `AgentSpec` | 身份、能力、输入输出、执行策略 |
| `ExecutionPolicy` | 单个 Agent 如何推进任务 |
| `CompositionPolicy` | 多个执行实体如何组织与调度 |
| `TaskSpec` | 目标、约束、产物和完成条件 |
| `MessageEnvelope` | 通信来源、目标、关联关系和内容引用 |
| `CapabilityDescriptor` | 工具、Skill、模型或 Agent 的能力描述 |
| `ProtocolAdapter` | 外部协议与内部对象的映射 |
| `ExplorationPolicy` | 探索目标、课程、预算与停止条件 |
| `Verifier` | 结果与经验的验收规则 |
| `ReleaseManifest` | 模型、插件、技能、策略和代码的版本组合 |

这些接口应能独立使用，而不是要求开发者继承一个包含所有功能的巨型 `BaseAgent`。

## 探索属于系统能力

此前强调的 RSIAgent 式探索机制应保留，但它应作用于整个 Agent 系统，而不只是单个 Agent 的记忆。

建议支持三个范围：

- **个体探索**：学习环境规律、工具操作和任务技能。
- **协作探索**：优化角色分配、证据传递、任务交接和上下文分配。
- **系统探索**：评估模型路由、组件组合、协作拓扑和代码候选。

同时明确分开“正在生产运行的版本”与“正在探索的候选版本”。探索可以改变候选系统，但不应直接修改生产实例，也不能自行降低本轮验收条件。

## 验收不能只看 Demo

这一定位下，最关键的验收测试应是：

1. 不修改 Core，增加一种模型或模态适配。
2. 不修改 Core，接入 MCP 工具和外部 A2A Agent。
3. 不修改 Core，实现新的 Agent 执行循环。
4. 不修改 Core，从单 Agent 切换为层级或事件驱动协作。
5. 不重写第三方 Agent，接入其任务、状态和产物。
6. 在同一运行时下，分别运行关闭探索与启用探索的应用。
7. 在权限、预算和版本边界内，发布并回滚经过验证的改进。

**后续设计应以“稳定 Core＋开放策略＋协议适配＋可选探索演化”为基准。** 你维护的是构建 Agent 系统的底层契约和运行机制，而不是替开发者预先决定他们必须构建什么样的 Agent。

<span style="display:none">[^3_10][^3_11][^3_12][^3_13][^3_14][^3_15][^3_4][^3_5][^3_6][^3_7][^3_8][^3_9]</span>

<div align="center">⁂</div>

[^3_1]: https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture

[^3_2]: https://a2a-protocol.org/latest/topics/key-concepts/

[^3_3]: https://a2a-protocol.org/v1.0.1/topics/key-concepts/

[^3_4]: https://a2a-protocol.org/pr-849/topics/key-concepts/

[^3_5]: https://www.ibm.com/think/topics/agent2agent-protocol

[^3_6]: https://www.paloaltonetworks.com/cyberpedia/what-is-model-context-protocol-mcp

[^3_7]: https://modelcontextprotocol.io/docs/2026-07-28/learn/server-concepts

[^3_8]: https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/

[^3_9]: https://modelcontextprotocol.io/docs/draft/learn/architecture

[^3_10]: https://modelcontextprotocol.io/specification/2026-07-28/architecture

[^3_11]: https://dev.to/aws-builders/mcp-explained-how-the-model-context-protocol-transforms-ai-in-the-cloud-4hba

[^3_12]: https://eseospace.com/blog/mcp-resources-explained/

[^3_13]: https://a2a-protocol.org/latest/specification/

[^3_14]: https://restfulapi.net/a2a-protocol/

[^3_15]: https://www.geeksforgeeks.org/artificial-intelligence/agent2agent-a2a/


---

# 看看这个Core层应该包含哪些核心接口和状态机定义

可以，把 Core 层先收敛成两部分来看：**核心接口契约** 和 **状态机定义**。前者回答“系统里有哪些稳定对象、它们怎样交互”，后者回答“一个 Agent 系统运行时，状态如何推进、何时允许调用、何时必须停下”。这也符合你前面一直强调的方向：开放协议与形态，但把副作用、权限、验证和演化都收敛到统一运行时里。[^4_1][^4_2]

我建议 Core 不要先从“类图”出发，而要从**最小稳定原语**出发：Task、Agent、Capability、Action、Artifact、Event、Evidence、Policy、Release。这样更容易同时兼容单 Agent、MCP 工具调用、A2A 委派和多 Agent 编排。[^4_3][^4_4][^4_5]

## 核心接口

Core 层建议至少包含下面 12 组接口；它们属于产品架构建议，不是某个现成协议的原样照搬。


| 接口 | 作用 | 为什么必须在 Core |
| :-- | :-- | :-- |
| `TaskSpec` | 定义任务目标、输入输出、约束、完成条件 | A2A 的任务有生命周期，但你的系统仍需自己的业务完成判定。[^4_4] |
| `AgentSpec` | 描述一个 Agent 的身份、能力、角色和默认策略 | 支持单 Agent、多 Agent、外部 Agent 统一寻址。[^4_1] |
| `ExecutionPolicy` | 定义一个 Agent 如何推进任务 | 防止把流程跳转完全交给 LLM 自由决定。[^4_6] |
| `CompositionPolicy` | 定义多个 Agent/Worker 如何协作 | 用来承载主管式、对等式、事件驱动式编排。[^4_2] |
| `CapabilityDescriptor` | 统一描述工具、Skill、MCP 能力、远程 Agent 能力 | 让 MCP、FC、Tool Use 收敛到同一能力目录。[^4_5][^4_1] |
| `ProtocolAdapter` | 将 MCP、A2A、自定义协议映射到内核对象 | 协议互操作不应污染任务状态机。[^4_5][^4_4] |
| `ActionRequest` | 一次拟执行动作的标准表示 | 把“模型建议调用”与“系统实际执行”分开。[^4_7][^4_3] |
| `Verifier` | 检查动作结果、任务完成度、证据充分性 | 避免执行者自己宣布成功。[^4_3] |
| `EvidenceStore` | 存证、血缘追踪、证据引用 | 让验证和回滚有依据。[^4_3][^4_8] |
| `PolicyEngine` | 权限、预算、风险、审批门控 | 副作用必须实时重授权，而非只在开始时检查一次。[^4_9] |
| `ReleaseManifest` | 固定一次运行所绑定的模型、能力、策略与版本 | 动态能力系统必须可回放、可审计。[^4_2] |
| `EvolutionController` | 管理候选技能、策略、代码修改与发布 | 把探索/自进化与生产运行隔离。[^4_10] |

更紧凑地说，Core 至少要稳定这六类“第一性对象”：

```text
谁在做（Agent）
在做什么（Task）
可以做什么（Capability）
准备怎么做（Action）
凭什么算做成（Evidence + Verifier）
按什么边界运行（Policy + Release）
```


## 建议的接口轮廓

下面我给的是**接口层面的骨架**，不是语言绑定实现。你可以用 Python Protocol / TypeScript interface / Rust trait 都行，关键是语义先稳定。

### 1) 任务与运行对象

```python
class TaskSpec(Protocol):
    id: str
    kind: str
    goal: dict
    input_schema: dict
    output_schema: dict | None
    constraints: dict
    acceptance_criteria: list[dict]
```

`TaskSpec` 不只是输入输出类型，还要包含预算、时限、风险等级、允许能力范围。这样同一个运行时才能同时承载只读问答、桌面操作、跨 Agent 委派等任务。[^4_3]

```python
class TaskHandle(Protocol):
    id: str
    state: str
    parent_task_id: str | None
    assigned_agent: str | None
    created_at: str
    updated_at: str
```

`TaskSpec` 是定义，`TaskHandle` 是实例。A2A 里的任务对象本身就是有状态生命周期单元，这与你的 Core 很契合。[^4_4]

### 2) Agent 与编排

```python
class AgentSpec(Protocol):
    id: str
    role: str
    capabilities: list[str]
    execution_policy: str
    composition_tags: list[str]
    protocol_endpoints: dict
```

```python
class ExecutionPolicy(Protocol):
    def propose_next_actions(self, task: TaskHandle, context: dict) -> list["ActionRequest"]: ...
    def should_yield(self, task: TaskHandle, context: dict) -> bool: ...
```

```python
class CompositionPolicy(Protocol):
    def select_participants(self, task: TaskHandle, agents: list[AgentSpec]) -> list[str]: ...
    def route_event(self, event: "RuntimeEvent") -> list[str]: ...
    def decide_termination(self, task: TaskHandle, context: dict) -> str: ...
```

这里的关键是：**ExecutionPolicy 负责个体推进，CompositionPolicy 负责协作组织。** 不要把“一个 Agent 怎样思考”和“多个 Agent 怎样协作”混成一个接口。[^4_2]

### 3) 能力与协议

```python
class CapabilityDescriptor(Protocol):
    id: str
    type: str          # tool | skill | mcp_tool | remote_agent | retriever | model
    input_schema: dict
    output_schema: dict
    side_effect_level: str
    auth_scope: list[str]
    version: str
```

```python
class CapabilityRegistry(Protocol):
    def register(self, capability: CapabilityDescriptor) -> None: ...
    def discover(self, query: dict) -> list[CapabilityDescriptor]: ...
    def resolve(self, capability_id: str) -> CapabilityDescriptor: ...
```

MCP 提供工具、资源和提示等原语，但在 Core 内部应尽量收敛为统一的能力描述与发现机制。[^4_5][^4_11]

```python
class ProtocolAdapter(Protocol):
    protocol_name: str
    def import_capabilities(self) -> list[CapabilityDescriptor]: ...
    def export_task(self, task: TaskHandle) -> dict: ...
    def import_event(self, payload: dict) -> "RuntimeEvent": ...
```

A2A、MCP、自定义 Agent 协议都可以通过 `ProtocolAdapter` 接入；这样协议变更不需要直接改状态机。[^4_4][^4_5]

### 4) 动作、验证与证据

```python
class ActionRequest(Protocol):
    id: str
    task_id: str
    actor_id: str
    capability_id: str
    arguments: dict
    mode: str          # preview | execute | simulate | delegate
    justification: dict
```

这个设计直接对应你之前关注的 preview / execute 双阶段思想：高风险动作先形成提案，再进入审批或执行。[^4_3]

```python
class ActionResult(Protocol):
    action_id: str
    status: str
    output: dict
    artifacts: list[str]
    evidence_refs: list[str]
    error: dict | None
```

```python
class Verifier(Protocol):
    def verify_action(self, request: ActionRequest, result: ActionResult) -> dict: ...
    def verify_task(self, task: TaskHandle, evidence_refs: list[str]) -> dict: ...
```

```python
class EvidenceStore(Protocol):
    def write_observation(self, observation: dict) -> str: ...
    def write_evidence(self, claim: dict, support: list[str]) -> str: ...
    def trace_lineage(self, evidence_id: str) -> dict: ...
```

你前面反复强调“不能让执行者自己宣布成功”，所以 `Verifier` 必须是 Core 一等接口，而不是业务层可有可无的插件。[^4_3]

### 5) 策略与版本

```python
class PolicyEngine(Protocol):
    def authorize(self, action: ActionRequest, context: dict) -> dict: ...
    def enforce_budget(self, task: TaskHandle, usage: dict) -> dict: ...
    def require_approval(self, action: ActionRequest) -> bool: ...
```

策略决策不应只发生在任务开始，而应发生在**每次有副作用动作执行前**。[^4_9]

```python
class ReleaseManifest(Protocol):
    release_id: str
    models: dict
    capabilities: dict
    policies: dict
    prompts: dict
    workflows: dict
```

动态工具、策略和模型环境中，`ReleaseManifest` 是“这一轮到底按什么版本运行”的锚点；没有它，长任务回放和审计会非常痛苦。[^4_2]

## Core 层状态机

状态机不要只定义“任务完成/失败”，而要覆盖**任务级、动作级、演化级**三层。否则系统一复杂，就会把大量关键控制流塞进自然语言。

### 1) 任务状态机

建议任务状态至少有这些：


| 状态 | 含义 | 可转移到 |
| :-- | :-- | :-- |
| `CREATED` | 已创建，尚未开始调度 | `PLANNING`, `CANCELLED` |
| `PLANNING` | 正在生成或更新执行计划 | `READY`, `WAITING_INPUT`, `FAILED` |
| `READY` | 可执行，等待调度 | `RUNNING`, `DELEGATING`, `BLOCKED` |
| `RUNNING` | 正在执行动作或子任务 | `VERIFYING`, `WAITING_APPROVAL`, `BLOCKED`, `FAILED` |
| `DELEGATING` | 正在委派到其他 Agent / A2A 端点 | `WAITING_CHILDREN`, `FAILED` |
| `WAITING_CHILDREN` | 等待子任务或远端结果 | `READY`, `VERIFYING`, `FAILED` |
| `WAITING_INPUT` | 需要用户或外部系统补充输入 | `READY`, `CANCELLED` |
| `WAITING_APPROVAL` | 等待审批高风险动作 | `READY`, `FAILED`, `CANCELLED` |
| `BLOCKED` | 缺少能力、权限、环境或证据 | `READY`, `FAILED`, `ESCALATED` |
| `VERIFYING` | 检查是否满足完成条件 | `COMPLETED`, `READY`, `FAILED` |
| `ESCALATED` | 交由人工或上级系统处理 | `READY`, `COMPLETED`, `FAILED` |
| `COMPLETED` | 验证通过完成 | 终态 |
| `FAILED` | 无法继续或验证失败 | 终态 |
| `CANCELLED` | 主动取消 | 终态 |

可以概括成：

```text
CREATED
  → PLANNING
  → READY
  → RUNNING
  → VERIFYING
  → COMPLETED
```

但真实系统必须支持分支：

```text
RUNNING → WAITING_APPROVAL
RUNNING → DELEGATING → WAITING_CHILDREN
RUNNING → BLOCKED
VERIFYING → READY   （验证不通过，需要继续）
```

这和你之前关于确定性路由器的要求一致：**LLM 可以提出下一步，但状态迁移由显式规则决定。**[^4_6]

### 2) 动作状态机

动作状态机比任务更细，重点在副作用控制。


| 状态 | 含义 |
| :-- | :-- |
| `PROPOSED` | Agent 提出动作 |
| `VALIDATING` | Schema、参数、上下文校验 |
| `REJECTED` | 因类型、权限、预算或策略被拒绝 |
| `PREVIEWING` | 生成 dry-run / 影响分析 |
| `AWAITING_APPROVAL` | 等待审批 |
| `AUTHORIZED` | 已通过策略引擎 |
| `EXECUTING` | 真实执行 |
| `OBSERVING` | 收集结果与环境变化 |
| `VERIFIED` | 动作结果被验证 |
| `ROLLED_BACK` | 已撤销或补偿 |
| `FAILED` | 执行或验证失败 |

建议采用：

```text
PROPOSED
  → VALIDATING
  → PREVIEWING
  → AWAITING_APPROVAL
  → AUTHORIZED
  → EXECUTING
  → OBSERVING
  → VERIFIED
```

其中不是每个动作都要走全流程：

- 只读检索：可跳过审批。
- 本地低风险解析：可直接授权执行。
- 外部写操作：应强制 preview / approval / verification。[^4_3]


### 3) 能力缺口状态机

既然你的系统要支持探索和自进化，`Gap` 也应有状态，而不是一条备注。


| 状态 | 含义 |
| :-- | :-- |
| `DISCOVERED` | 已识别到缺口 |
| `TRIAGED` | 已归类为知识/技能/工具/权限/环境/验证缺口 |
| `ACTIONABLE` | 可以自动探索或修复 |
| `WAITING_EXTERNAL` | 需用户、管理员或外部系统补齐 |
| `PATCH_CANDIDATE_READY` | 已形成候选修复 |
| `UNDER_EVALUATION` | 正在测试和回归 |
| `RESOLVED` | 已发布解决方案 |
| `WONT_FIX` | 暂不处理 |

这正对应你前面提到的“持续记录缺少能力，并由 RSI 自己创造或提示用户补齐”的路线。[^4_10]

### 4) 演化候选状态机

如果系统允许修改 Skill、策略甚至 Core 代码，候选版本必须独立有生命周期。


| 状态 | 含义 |
| :-- | :-- |
| `DRAFT` | 候选已生成 |
| `BUILT` | 已完成构建 |
| `TESTING` | 正在契约、回归、安全测试 |
| `STAGED` | 已进入预演环境 |
| `APPROVED` | 满足发布条件 |
| `RELEASED` | 已发布 |
| `ROLLED_BACK` | 发布后撤回 |
| `REJECTED` | 未通过 |

这里要坚持一条核心边界：**候选可以修改产品，但不能同时修改自己的验收标准并据此证明通过。**[^4_10]

## 事件模型

要把这些状态机真正跑起来，Core 还需要统一事件接口；否则状态会散落在日志、数据库和模型文本里。

建议最小事件模型：

```python
class RuntimeEvent(Protocol):
    id: str
    type: str
    ts: str
    subject_type: str   # task | action | agent | gap | release
    subject_id: str
    causation_id: str | None
    correlation_id: str | None
    payload: dict
```

常见事件包括：

- `TaskCreated`
- `TaskStateChanged`
- `ActionProposed`
- `ActionAuthorized`
- `ActionExecuted`
- `ActionVerified`
- `DelegationStarted`
- `DelegationCompleted`
- `GapDiscovered`
- `CandidateEvaluated`
- `ReleasePublished`

A2A 的消息与任务更新、MCP 的工具调用结果、自定义协议的通知，都应尽量落到这套统一事件上，再由订阅器或投影器生成视图。[^4_5][^4_4]

## 最小可实现版本

如果你现在要做 V1 Core，我建议先固定下面这组“最小闭环接口”：

1. `TaskSpec`
2. `AgentSpec`
3. `CapabilityDescriptor`
4. `ExecutionPolicy`
5. `ActionRequest`
6. `PolicyEngine`
7. `Verifier`
8. `EvidenceStore`
9. `ProtocolAdapter`
10. `ReleaseManifest`

配套先实现两层状态机：

1. 任务状态机：`CREATED → PLANNING → READY → RUNNING → VERIFYING → COMPLETED/FAILED`
2. 动作状态机：`PROPOSED → VALIDATING → AUTHORIZED → EXECUTING → OBSERVING → VERIFIED/FAILED`

这是最小但完整的 Core 主骨架；它已经足够承载：

- 单 Agent 任务执行。
- FC / Tool Use 收敛执行。
- MCP 工具接入。
- A2A 子任务委派。
- 多 Agent 协作。
- 只读与有副作用动作分流。
- 后续再挂探索、缺口管理与演化控制。[^4_1][^4_6]


## 优先固定的 6 份契约

如果只先写最关键的文档/代码接口，我建议你优先把这 6 份做成稳定契约：


| 契约 | 先解决什么问题 |
| :-- | :-- |
| `TaskSpec` | 任务如何被定义与完成 |
| `ActionRequest` | 模型建议如何变成可控动作 |
| `CapabilityDescriptor` | MCP/Tool/A2A/Skill 如何统一描述 |
| `ExecutionPolicy` | LLM 与确定性路由如何分权 |
| `Verifier` | 什么叫“真的完成” |
| `ReleaseManifest` | 动态系统如何可回放、可审计 |

这 6 份一旦稳定，你的 Core 基本就从“概念平台”进入“可工程化产品骨架”了。[^4_2][^4_3]

如果你愿意，下一步我可以继续直接给你一版**更贴近代码实现的 Python/TypeScript 接口定义草案**，以及对应的**任务状态机与动作状态机 UML/时序图文本版**。

<div align="center">⁂</div>

[^4_1]: https://www.perplexity.ai/search/8f47bd07-22fa-4e7b-88c8-06c8c4be7ed1

[^4_2]: https://www.perplexity.ai/search/5b617c1f-cbd2-4cde-b591-0df6bbcf434a

[^4_3]: https://www.perplexity.ai/search/33065d4a-cc56-4a4c-a9a4-c9bb5fbf5ce3

[^4_4]: https://a2a-protocol.org/v1.0.1/topics/key-concepts/

[^4_5]: https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture

[^4_6]: https://www.perplexity.ai/search/6b909230-ad9f-4c9f-b347-2e1895baa9e1

[^4_7]: https://developers.openai.com/api/docs/guides/function-calling?api-mode=responses

[^4_8]: https://www.perplexity.ai/search/357e5cab-28ee-4357-af14-fc1e5b0389c2

[^4_9]: https://www.perplexity.ai/search/3ce924e2-79da-4cd8-ae47-28c2da1ddc76

[^4_10]: https://www.perplexity.ai/search/f00a0b6a-8809-42e0-a9ee-93d8a44b1cc8

[^4_11]: https://modelcontextprotocol.io/docs/2026-07-28/learn/server-concepts


---

# 整个基座应该有对于记忆以及agentruntime等方面的储备，且支持用户继续二次开发拓展这些能力

**对，记忆系统与 Agent Runtime 都应成为基座的一等子系统：既提供开箱可用的参考实现，也提供第三方能够替换、组合和扩展的稳定接口。** 不能只预留一个 `memory` 参数，也不能把 Runtime 简化成 `while → 调模型 → 调工具` 的循环。

上一版接口清单需要补齐这两块，并修正一个重要设计：**Core 固定运行约束，不固定 Agent 思考流程；提供持久化机制，不把所有持久化数据都叫作记忆。**

以下是建议的架构与契约，不是现有项目的原样实现。

## 先分清四类状态

建议明确区分：运行状态、认知记忆、证据记录和业务数据。

作为参考，LangGraph 将线程级检查点与跨线程长期存储分开，说明“恢复当前执行”与“记住跨任务知识”本来就是不同职责；我们可以借鉴这种分离，而不依赖它的图执行架构。[^5_1]


| 类型 | 示例 | 主要责任 |
| :-- | :-- | :-- |
| 运行状态 | 等待哪个子任务、执行到哪一步、预算余额 | Runtime / StateStore |
| 认知记忆 | 用户偏好、环境规律、历史经验 | Memory |
| 原始证据 | 截图、音频、工具回执、环境观察 | Evidence / Artifact |
| 业务数据 | 订单、库存、客户档案 | 业务系统 / DataConnector |

**任务检查点不是长期记忆，向量索引不是记忆本体，记忆也不能替代业务数据库。**

例如，“订单已经支付”应以业务系统的有效状态为准，不能因为记忆里曾经记录过，就直接作为当前事实。

## 记忆系统的分层

建议将记忆设计成独立的 **Memory Subsystem**，至少覆盖以下类型，但不强制应用全部启用。


| 记忆类型 | 保存内容 | 可扩展方向 |
| :-- | :-- | :-- |
| 工作记忆 | 当前目标、临时推断、待办 | 自定义工作区与状态结构 |
| 会话记忆 | 对话、交互事件、会话摘要 | 摘要、裁剪、压缩策略 |
| 情节记忆 | 过去发生的任务、操作与结果 | 轨迹检索、相似案例召回 |
| 语义记忆 | 已确认知识、偏好、环境约束 | 结构化、向量、图谱 |
| 程序性记忆 | 操作流程、经验规则、技能引用 | Skill 合成与修订 |
| 协作记忆 | 团队共享事实、交接信息 | 黑板、共享空间、冲突合并 |

这几类是**逻辑分类**，不代表必须部署六套数据库。同一个后端可以承载多种记忆，但其作用域、写入规则与生命周期应独立。

对于全模态，建议让记忆保存**原始媒体引用＋派生表示＋时空定位＋来源关系**，而非只保留转写或摘要。例如，一条操作经验可以引用某段视频、具体时间区间和相关画面区域。

## 记忆的核心接口

记忆接口不能只有 `add/search/delete`。建议划分为存储、召回、加工和治理四组。


| 接口 | 核心方法示意 | 第三方可扩展什么 |
| :-- | :-- | :-- |
| `MemoryStore` | `get / put / query / tombstone` | 存储后端与一致性能力 |
| `MemoryRetriever` | `retrieve / rerank` | 语义、时间、关系、混合召回 |
| `MemoryWriter` | `propose / validate / commit` | 哪些内容值得记、如何验证 |
| `MemoryConsolidator` | `summarize / merge / detect_conflicts` | 摘要、去重、经验提炼 |
| `MemoryPolicy` | `can_read / can_write / retention` | 作用域、共享、保留与遗忘 |
| `MemoryVersioning` | `revise / supersede / invalidate` | 修订、失效、依赖传播 |
| `ContextAssembler` | `assemble` | 如何把记忆转成模型上下文 |

其中，**Memory 与 ContextAssembler 必须分开**：

```text
记忆库
  → 按身份与任务过滤
  → 召回候选
  → 检查时效、冲突和适用条件
  → 按上下文预算组织
  → 提供给模型或执行策略
```

“存了什么”和“本轮给模型看什么”是不同问题。开发者应当能替换召回算法，而不改存储；也能替换上下文组织策略，而不改 Runtime。

### 记忆对象

建议最小对象包含：

```text
MemoryRecord
  id / revision / schema_version
  kind / namespace / owner / access_policy
  content_parts / artifact_refs
  source_refs / evidence_refs
  epistemic_status
  applicability / environment_version
  valid_from / expires_at
  supersedes / dependency_refs
```

`epistemic_status` 用于区分观察、假设和已验证主张，不宜只用一个 `confidence=0.9` 代替。

作用域建议支持应用、用户、Agent、团队、会话与任务的组合，但**共享范围不能自动扩大**：某个 Agent 能读取的信息，不代表可以写入团队公共记忆。

## 记忆生命周期

不建议用单一状态机混合“是否可信”和“是否过期”。至少拆成两个维度：


| 维度 | 建议状态 |
| :-- | :-- |
| 验证状态 | `CANDIDATE / VERIFIED / DISPUTED / REJECTED` |
| 生命周期 | `ACTIVE / SUPERSEDED / EXPIRED / TOMBSTONED` |

典型流程：

```text
生成候选
  → 检查来源、敏感性与适用范围
  → 验证
  → 发布为可用记忆
  → 持续发现反例
  → 修订、替代或失效
```

“已验证但过期”和“尚未验证但仍待研究”必须能分别表达。对于删除，还需要区分逻辑失效与实际清除：原文、向量索引、缓存和派生摘要应有一致的删除策略。

## Runtime 的职责边界

建议将 Agent Runtime 定义为：

> 管理 Agent 实例及其任务执行，负责生命周期、调度、状态持久化、通信、能力执行、资源约束与故障恢复的运行子系统。

它不是某个 Agent 推理方法的同义词。可以参考 Actor Runtime 中“逻辑身份与内存中运行实例分离”的思路：Orleans 就将实体身份与激活生命周期分开，并提供持久化和持久调度机制。[^5_2][^5_3]

你的 Core 建议明确区分：


| 对象 | 含义 |
| :-- | :-- |
| `AgentDefinition` | Agent 的版本化定义 |
| `AgentIdentity` | 可寻址的逻辑身份 |
| `AgentActivation` | 当前 Worker 上的活动实例 |
| `Session` | 连续交互上下文 |
| `Task` | 需要完成的工作 |
| `Run` | 一次具体执行实例 |
| `Attempt` | 某个执行步骤或调用的尝试 |

**一个 Agent 可以执行多个任务，一个任务可以产生多个 Run；Worker 重启也不应改变 Agent 的逻辑身份。**

## Runtime 的核心接口

建议至少提供以下扩展面：


| 接口 | 负责什么 | 可替换内容 |
| :-- | :-- | :-- |
| `AgentRuntime` | 激活、提交、发信号、查询、终止 | 本地或远程运行实现 |
| `ExecutionDriver` | 推进单个 Agent 的执行 | 模型循环、规则、规划执行、自定义逻辑 |
| `Scheduler` | 调度、优先级、并发、公平性 | 调度算法 |
| `StateStore` | 状态读写、版本冲突检查 | 数据库与状态后端 |
| `CheckpointStore` | 保存与恢复执行检查点 | 检查点格式与存储 |
| `Mailbox` | 消息投递、去重、消费确认 | 本地队列或消息系统 |
| `TimerService` | 超时、唤醒、周期触发 | 持久调度实现 |
| `CapabilityExecutor` | 执行工具或委派能力 | 本地、沙箱、MCP、A2A 适配 |
| `ResourceGovernor` | 预算、配额、并发与限流 | 资源策略 |
| `ExecutionBackend` | 进程、容器、远程 Worker 的执行承载 | 运行环境 |
| `RuntimeObserver` | 事件、指标与诊断 | 可观测后端 |

这些接口应支持**能力声明**。例如，不提供检查点的第三方执行器，可以被接入，但不能被标为“支持任意位置恢复”。

## 修正运行状态机

上一版把 `PLANNING` 作为必经状态，会限制无规划、事件驱动和纯规则 Agent。建议改成：**内核管理通用生命周期，规划等阶段作为策略自己的子状态。**

### Run 状态

```text
CREATED → QUEUED → RUNNING
                      ↕
                   SUSPENDED

RUNNING → SUCCEEDED / FAILED
非终态 → CANCELLING → CANCELLED
```

`SUSPENDED` 配套结构化等待原因：

```text
INPUT / APPROVAL / CHILD_TASK / MESSAGE
TIMER / RESOURCE / RECONCILIATION
```

还需规定：

- 成功必须满足当前 `TaskSpec` 的完成契约，不一定每次调用额外的模型验证器。
- 取消是请求，不保证立刻停止已经发出的远程动作。
- 暂停与恢复需要检查点或相应执行器能力。
- 终态不直接改回运行态；重新执行创建新 Run，并关联原 Run。


### 调用状态

动作调用建议单独管理：

```text
PROPOSED → VALIDATED → READY → DISPATCHED
                                  ↓
                          SUCCEEDED / FAILED
                                  或
                          OUTCOME_UNKNOWN
```

审批可以是进入 `READY` 前的门禁；预览是能力声明支持时采用的步骤，而非所有外部写操作都假定具备。

**超时不等于失败。** 外部操作可能已经生效但回执丢失，应进入 `OUTCOME_UNKNOWN`，通过状态查询或证据核对解决，不能直接重试。补偿操作也应有独立记录，不能把原动作简单改成“从未发生”。

## 两者如何协同

建议每个执行步骤遵循以下边界：

```text
读取 Run 状态与版本
  → 组装授权上下文
  → 召回适用记忆
  → ExecutionDriver 生成下一步提议
  → Core 检查并执行
  → 保存结果、事件与检查点
  → 按 MemoryWriter 策略生成候选记忆
```

关键约束是：

- **恢复执行用检查点，不用摘要猜测执行位置。**
- **长期记忆更新失败，不应导致已完成的付款等动作再次执行。**
- **某次 Run 使用的记忆版本与内容引用应可追踪。**
- **更新记忆不应静默改变正在执行的计划；需要显式刷新或重规划。**

本地状态提交、事件发布、记忆加工之间要声明一致性边界；外部副作用不能因为数据库事务存在，就被宣称为端到端“恰好一次”。

## 二次开发的交付方式

“可扩展”应包括三个层次，而不只是允许用户传入回调：


| 层次 | 开发者能做什么 |
| :-- | :-- |
| 配置 | 选择记忆类型、后端、运行策略与预算 |
| 插件 | 实现召回器、记忆整理器、执行 Driver、调度器 |
| 实现替换 | 替换 Memory 或 Runtime 后端，保留公共契约 |

建议每个扩展点同时交付：

- 接口与数据 Schema。
- 默认实现和最小示例。
- 支持能力声明与不支持行为。
- 契约测试与故障注入测试。
- 版本兼容和数据迁移规范。
- 权限、并发、取消与超时语义。

最后，上一版接口优先级应调整为：**先稳定 Runtime 的身份、任务、Run、状态与恢复契约，再稳定 Memory 的作用域、记录、召回、写入与版本契约；探索和演化直接建立在这两组能力上。** 这不是缩减探索目标，而是让探索得到的经验能够可靠积累，让改进后的 Agent 能够真正持续运行。

<span style="display:none">[^5_10][^5_11][^5_12][^5_13][^5_14][^5_15][^5_4][^5_5][^5_6][^5_7][^5_8][^5_9]</span>

<div align="center">⁂</div>

[^5_1]: https://docs.langchain.com/oss/python/langgraph/persistence.md

[^5_2]: https://learn.microsoft.com/en-us/dotnet/orleans/overview

[^5_3]: https://dotnet.github.io/orleans/docs/overview/

[^5_4]: https://docs.langchain.com/oss/javascript/langgraph/persistence.md

[^5_5]: https://docs.langchain.com/oss/javascript/langgraph/add-memory

[^5_6]: https://docs.langchain.com/oss/python/langgraph/add-memory

[^5_7]: https://docs.langchain.com/oss/python/concepts/memory

[^5_8]: https://docs.langchain.com/oss/python/concepts/memory.md

[^5_9]: https://docs.langchain.com/oss/python/langgraph/persistence

[^5_10]: https://www.mongodb.com/docs/atlas/ai-integrations/langgraph-js/long-term-memory-store/

[^5_11]: https://docs.langchain.com/oss/javascript/langgraph/functional-api-memory

[^5_12]: https://docs.langchain.com/oss/python/langgraph/stores

[^5_13]: https://learn.microsoft.com/en-us/dotnet/api/orleans.grain.getreminders?view=orleans-3.0

[^5_14]: https://ai.plainenglish.io/understanding-short-term-memory-in-langgraph-a-hands-on-guide-5536f39d0cb3

[^5_15]: https://blog.kritner.com/2018/10/17/Microsoft-Orleans-Reusing-Grains-and-Grain-State/


---

# 探索机制如何与记忆系统结合以自进化

探索机制和记忆系统的结合，核心不是“探索完就把结果存起来”，而是把**探索—验证—记忆更新—再探索**做成一个受控闭环。RSIAgent 的关键点就在于：通过 curriculum、actor、verifier 的协作，把环境交互变成持续学习过程；广泛探索阶段并行积累经验，深度探索阶段再围绕薄弱点顺序修正，并将验证后的知识写入持久记忆，而不更新模型权重。[^6_1][^6_2][^6_3][^6_4]

Voyager 和 Reflexion 也说明了两种重要结合方式：前者把探索结果沉淀为可复用的技能库，后者把失败反馈转成语言化反思记忆供下一轮使用。[^6_5][^6_6][^6_7]

## 基本闭环

建议把“自进化”拆成下面 6 个环节：

```text
探索任务生成
  → 执行与环境交互
  → 独立验证
  → 记忆候选生成
  → 记忆筛选与发布
  → 基于新记忆的下一轮探索
```

这里最重要的是：**记忆不是探索日志的原样堆积，而是探索产物的分层沉淀。**

可借鉴 RSIAgent 的 broad-then-deep 思路：先通过广泛探索建立环境地图和粗粒度经验，再通过针对性探索验证边界条件、失败模式和隐藏前置条件。[^6_4][^6_1]

## 记忆如何服务探索

探索并不是从零开始，它应先读取已有记忆，再决定“哪里未知、哪里脆弱、哪里值得花预算”。

建议把记忆对探索的作用拆成四类：


| 记忆作用 | 具体用途 |
| :-- | :-- |
| 能力边界 | 已知会什么、不会什么、在哪些条件下会失效 |
| 环境规律 | 某个系统、界面、数据源、工具的行为模式 |
| 失败教训 | 哪些路径无效、哪些参数危险、哪些前提常缺失 |
| 可复用技能 | 已验证流程、脚本、操作套路、委派模式 |

Voyager 的 skill library 就体现了这种“探索后形成可复用能力”的思路：自动课程提出下一个目标，成功后把行为沉淀为技能，遇到失败再结合反馈修正技能。[^6_6][^6_8]

因此，探索器在生成新任务前应先问三件事：

1. 当前目标依赖哪些已知能力。
2. 哪些能力置信度低、适用范围不清。
3. 哪些失败模式反复出现但尚未被解释。

这意味着记忆不仅是“给执行器看的上下文”，更是**课程生成器的输入**。

## 探索如何写入记忆

我建议不要把每次探索结果直接写入长期记忆，而是先进入候选层。因为 RSIAgent 自己也强调了：真正有价值的是经过 verifier 检查后、能复用的环境知识，而不是所有试错痕迹。[^6_3][^6_1]

可以把写入流程设计成三层：


| 层 | 写入什么 | 什么时候进入下一层 |
| :-- | :-- | :-- |
| 原始轨迹层 | 操作、观察、回执、截图、错误 | 执行结束立即保存 |
| 候选经验层 | 结构化结论、假设、经验草稿 | 验证通过后 |
| 发布记忆层 | 可复用规则、技能、限制条件 | 冲突检查和版本化后 |

例如一次探索失败，不应直接生成“此功能不可用”的长期规则，而应先写成：

```text
观察：在版本 X、权限 Y、页面状态 Z 下，按钮点击后无预期结果。
假设：可能需要额外前置步骤或权限。
证据：截图、DOM 变化、网络回执、错误日志。
```

只有在多次验证后，才可能升级成：

```text
规则：在权限 Y 缺失时，该按钮不会触发导出；需先完成步骤 A。
```

这和 Reflexion 的思路也一致：失败反馈先形成语言化反思，再影响下一轮，不必直接伪装成确定知识。[^6_7][^6_5]

## 自进化的三种记忆更新

建议把“自进化”区分成三类，不要都叫 learning。

### 1) 反思型更新

来源是失败或偏差，产出是“以后别这样做”的经验。

典型格式：

- 触发条件。
- 错误原因。
- 未来避免策略。
- 证据引用。
- 置信与适用边界。

这最接近 Reflexion：把反馈转成语言记忆，指导下一轮决策。[^6_5]

### 2) 技能型更新

来源是成功探索，产出是可执行或可组合的程序性记忆。

典型格式：

- 技能描述。
- 前置条件。
- 执行步骤或脚本。
- 预期输出。
- 已知失效场景。

这最接近 Voyager 的 skill library：探索不只是知道答案，而是形成可再次调用的行为单元。[^6_6]

### 3) 结构型更新

来源是长期运行中的模式发现，产出是对系统组织方式的修正。

例如：

- 哪类任务应先走哪个 Agent。
- 哪种委派结构更稳定。
- 哪类模型更适合某种子任务。
- 哪类记忆召回策略成本低且效果好。

这类更新就不仅优化单个技能，而是在改进 Agent Runtime 的运行结构。

## 用记忆驱动下一轮探索

真正的自进化不是“记住更多”，而是“记忆改变了下一轮搜索方向”。

建议课程生成器显式读取以下对象：


| 输入 | 作用 |
| :-- | :-- |
| 成功技能覆盖图 | 找出尚未覆盖的能力区域 |
| 失败热点图 | 找出高频失败链路 |
| 不确定规则列表 | 优先验证模糊经验 |
| 冲突记忆对 | 专门设计反例任务 |
| 高价值未解决缺口 | 触发深度练习 |

RSIAgent 的 DRS 本质上就是：目标尝试暴露出缺口，再由 curriculum agent 顺着这些缺口安排后续练习，每次验证后的经验都会影响下一次练习内容。[^6_2][^6_4]

因此可以设计一个简单的选择函数：

```text
探索优先级 =
  业务价值
+ 失败频率
+ 不确定性
+ 可修复性
- 执行成本
- 风险等级
```

这样，记忆不是被动仓库，而是主动改变探索调度的“控制面输入”。

## 为了避免错误积累

探索与记忆耦合最大的问题不是学不到，而是**学错后越学越偏**。所以建议加四道门：


| 门禁 | 作用 |
| :-- | :-- |
| 主张分级 | 区分观察、假设、规则、技能，不允许自动越级 |
| 独立验证 | 执行者不能独自确认自己的结论 |
| 适用条件 | 每条记忆绑定环境版本、权限、输入条件 |
| 反例驱动 | 一旦发现冲突，优先安排验证而非继续复用 |

RSIAgent 的 verifier 机制和“探索后再整合记忆”的流程，本质上就是为了降低错误经验直接污染长期记忆的风险。[^6_1][^6_4]

更进一步，我建议你的 Core 中每条长期记忆至少带这些字段：

```text
claim_type
evidence_refs
applicability
confidence_band
verification_status
supersedes
invalidated_by
environment_version
```

这样后续探索若发现反例，就能把旧记忆降级、替代或失效，而不是简单覆盖。

## 运行时里的结合方式

从系统设计上，探索机制与记忆系统的结合应通过明确接口完成，而不是在 Agent Prompt 里“顺便总结一下经验”。

建议运行链路如下：

```text
ExplorationPolicy
  → 选择练习目标
  → ExecutionRuntime 执行
  → Verifier 验证结果
  → MemoryWriter 生成候选经验
  → MemoryConsolidator 合并/冲突检查
  → MemoryPublisher 发布版本
  → ExplorationPolicy 读取新记忆继续出题
```

其中每个环节都应可被第三方替换：

- 不同行业可以自定义 `Verifier`。
- 不同应用可以自定义 `MemoryWriter`。
- 不同团队可以替换 `ExplorationPolicy`。
- 核心 Runtime 只负责状态、事件、权限和版本。

这也符合你前面要求的“基座预置能力，但允许继续二次开发扩展”。

## 最后应落成哪些对象

如果你要把这套能力做进 Core，我建议至少新增 7 个正式对象：


| 对象 | 职责 |
| :-- | :-- |
| `ExplorationPolicy` | 决定探索什么、何时停 |
| `PracticeTask` | 一次探索或练习任务的定义 |
| `ExperienceCandidate` | 探索后形成的候选经验 |
| `MemoryWriter` | 将轨迹转成结构化经验 |
| `MemoryConsolidator` | 去重、合并、冲突检查、摘要 |
| `GapLedger` | 持续记录能力缺口与未解问题 |
| `MemoryPublisher` | 将候选经验发布为版本化长期记忆 |

这样，自进化的主线就清楚了：

```text
探索发现新东西
→ 验证什么是真的
→ 记忆系统把它变成可复用资产
→ 下一轮探索利用这些资产扩展能力边界
→ 直到形成更强的技能、策略和系统结构
```

最关键的一句话是：**探索机制负责制造“新经验”，记忆系统负责把“经验”变成“可治理、可复用、可纠错的能力资产”，两者闭环后才构成真正的自进化。**

<span style="display:none">[^6_10][^6_11][^6_12][^6_13][^6_14][^6_15][^6_9]</span>

<div align="center">⁂</div>

[^6_1]: https://www.alphaxiv.org/abs/2609.15364

[^6_2]: https://github.com/AetherLabsAI/RSIAgent/blob/main/docs/PAPER.md

[^6_3]: https://aetherlabsai.github.io/RSIAgent/

[^6_4]: https://github.com/AetherLabsAI/RSIAgent

[^6_5]: https://arxiv.org/abs/2303.11366

[^6_6]: https://voyager.minedojo.org/

[^6_7]: https://www.promptingguide.ai/techniques/reflexion

[^6_8]: https://gaos6e.github.io/AI-Agent-Engineer/en/landmarks/05-voyager-automatic-curriculum-and-skill-library

[^6_9]: https://github.com/Shiyao-Huang/awesome-agent-evolution/blob/main/research/papers/05-reflexion.md

[^6_10]: https://news.pedaily.cn/202609/569058.shtml

[^6_11]: https://github.com/razor-ai/rsiagent

[^6_12]: https://github.com/DeveloperHarris/Voyager

[^6_13]: https://arxiv.org/html/2609.14858v1?ref=sharedsapience.com

[^6_14]: https://taibui.dev/phases/14-agent-engineering/10-skill-libraries-voyager

[^6_15]: https://chatpaper.com/es/paper/344679


---

# 设计一个具体的自主课程生成器接口

可以。一个**自主课程生成器（Autonomous Curriculum Generator）** 的接口，不能只做成 `generate_next_task()` 这么薄；它至少要同时回答四个问题：

1. 现在该练什么。
2. 为什么练这个。
3. 这次练习如何验收。
4. 练完之后如何决定下一轮。

Voyager 的自动课程强调“基于当前已知能力与新颖性生成下一个任务”，而 RSIAgent 又进一步把课程拆成广泛探索与针对性深练两类，并让课程生成器根据目标、已有记忆和历史结果来选择 prerequisite、variant、contrast、stress 这类练习项目。[^7_1][^7_2][^7_3]

所以我建议你设计的不是一个“任务推荐器”，而是一个**课程控制面接口**。

## 设计目标

这个接口应满足四个原则：

- **目标驱动**：不是漫无目的出题，而是围绕业务目标、环境理解和能力缺口组织课程。[^7_3]
- **记忆感知**：课程生成必须读取已发布记忆、失败轨迹和当前能力覆盖。[^7_4]
- **可验证**：输出的不只是“练习题”，还要带验收和停止条件。
- **可扩展**：用户可以替换选题逻辑、评分逻辑、课程风格和难度策略。


## 顶层接口

我建议先定义一个顶层接口：

```python
class CurriculumGenerator(Protocol):
    def plan_wave(self, request: "CurriculumRequest") -> "CurriculumPlan": ...
    def refine_after_result(
        self,
        request: "CurriculumRefineRequest"
    ) -> "CurriculumPlan | CurriculumDecision": ...
    def should_stop(self, request: "CurriculumStopRequest") -> "CurriculumDecision": ...
```

这样拆成三个入口，而不是一个函数，原因是：

- `plan_wave` 用于初始课程规划，尤其适合 BRS 式并行任务波次。
- `refine_after_result` 用于根据上一轮结果继续深挖，适合 DRS 式顺序练习。[^7_5][^7_6]
- `should_stop` 让课程生成器显式决定继续、暂停、切模式还是结束，不要把停止逻辑散落到 Runtime 里。[^7_4]


## 输入契约

### 1) 初始请求

```python
@dataclass
class CurriculumRequest:
    target: "LearningTarget"
    mode: "CurriculumMode"              # broad | deep | mixed
    learner_state: "LearnerState"
    memory_snapshot: "MemorySnapshot"
    capability_map: "CapabilityMap"
    recent_outcomes: list["PracticeOutcome"]
    budget: "ExplorationBudget"
    policy: "CurriculumPolicy"
```

这里每一项都很关键。


| 字段 | 作用 |
| :-- | :-- |
| `target` | 定义当前为了什么而学，不一定等于最终业务任务 |
| `mode` | 指明当前是广度探索还是深度修正 |
| `learner_state` | 当前 Agent/团队有什么能力、什么限制 |
| `memory_snapshot` | 课程生成时使用的记忆视图，避免并发漂移 |
| `capability_map` | 用于发现覆盖盲区 |
| `recent_outcomes` | 用于避免重复练习，或针对最新失败出题 |
| `budget` | 控制课程规模、风险和成本 |
| `policy` | 注入行业/产品对课程的特殊限制 |

`memory_snapshot` 建议是只读快照；RSIAgent 在广泛探索阶段强调并行项目从同一共享起始记忆出发，这样结果更可比较，也避免任务间相互污染。[^7_7][^7_6]

### 2) 学习目标

```python
@dataclass
class LearningTarget:
    id: str
    kind: str                    # business_goal | environment_mastery | capability_gap | regression_guard
    objective: dict
    success_criteria: list[dict]
    priority: int
    scope: dict
```

目标不应只是一句自然语言。建议至少区分：

- **业务目标驱动**：为了某项真实任务做前置练习。
- **环境掌握驱动**：先认识系统结构。
- **缺口修补驱动**：补某类失败。
- **回归防护驱动**：验证旧能力是否退化。


## 输出契约

### 1) 课程计划

```python
@dataclass
class CurriculumPlan:
    plan_id: str
    mode: "CurriculumMode"
    rationale: list["CurriculumReason"]
    tasks: list["PracticeTask"]
    expected_learning_goals: list["LearningGoal"]
    stop_hints: list["StopHint"]
    next_review_after: str | None
```

这里的重点是 `rationale` 和 `tasks`。

课程生成器不能只输出一组任务 ID，它必须解释“为什么这些任务值得练”。这既利于调试，也利于后续自进化评估。

### 2) 练习任务对象

```python
@dataclass
class PracticeTask:
    id: str
    family: str                  # prerequisite | variant | contrast | stress | target_attempt | regression
    title: str
    objective: dict
    setup: dict
    constraints: dict
    acceptance_spec: "AcceptanceSpec"
    priority: float
    difficulty: float
    estimated_cost: "CostEstimate"
    expected_signal: "LearningSignal"
    depends_on: list[str]
    tags: list[str]
```

`family` 很重要，我建议直接把 RSIAgent 论文中明显有价值的任务族变成内核概念：

- `prerequisite`：补前置能力。
- `variant`：练近邻变化。
- `contrast`：区分容易混淆的解释。
- `stress`：测边界和鲁棒性。
- `target_attempt`：直接尝试目标。
- `regression`：防止旧能力退化。[^7_7][^7_3]

这样课程生成器不仅能“出题”，还能说明自己是按什么探索意图在出题。

## 验收接口

课程生成器必须输出验收规格，不应只让执行器“做了再看”。

```python
@dataclass
class AcceptanceSpec:
    verifier_profile: str
    required_evidence: list["EvidenceRequirement"]
    pass_conditions: list[dict]
    fail_conditions: list[dict]
    uncertainty_policy: str
```

其中 `required_evidence` 可以要求：

- 截图前后对比。
- 工具返回值。
- 文件内容变化。
- API 回执。
- 音视频片段。
- 子任务产物。

这样课程任务一生成，就天然进入你前面定义的 Runtime＋Verifier＋MemoryWriter 闭环，而不是临时拼提示词。[^7_8]

## 课程生成器的内部策略接口

顶层 `CurriculumGenerator` 之下，我建议进一步拆成几个可替换策略，而不是一整块黑盒。


| 子接口 | 负责什么 |
| :-- | :-- |
| `NoveltyEstimator` | 判断哪些区域新、哪些重复 |
| `GapSelector` | 从失败和能力图里挑缺口 |
| `DifficultyPlanner` | 安排从易到难还是对比优先 |
| `TaskFamilyMixer` | 决定 prerequisite / variant / contrast / stress 的比例 |
| `BudgetAllocator` | 把预算分配给不同任务波次 |
| `StopPolicy` | 判断何时停、何时切模式 |

例如：

```python
class GapSelector(Protocol):
    def select_gaps(
        self,
        target: LearningTarget,
        memory: MemorySnapshot,
        outcomes: list[PracticeOutcome],
        capability_map: CapabilityMap
    ) -> list["GapCandidate"]: ...
```

```python
class TaskFamilyMixer(Protocol):
    def mix(
        self,
        mode: str,
        gaps: list["GapCandidate"],
        budget: ExplorationBudget
    ) -> dict[str, int]: ...
```

这样开发者就可以只替换“缺口选择器”，而复用其他课程逻辑。

## 广度探索接口

为了适配 BRS，建议显式支持“任务波次”。

```python
@dataclass
class PracticeWave:
    wave_id: str
    shared_memory_snapshot_id: str
    tasks: list[PracticeTask]
    parallelism: int
    merge_policy: str
```

然后 `CurriculumPlan` 可以包含一个或多个 `PracticeWave`。

```python
@dataclass
class CurriculumPlan:
    plan_id: str
    mode: CurriculumMode
    waves: list[PracticeWave]
    rationale: list[CurriculumReason]
    expected_learning_goals: list[LearningGoal]
    stop_hints: list[StopHint]
```

这么设计有两个好处：

1. 直接支持 RSIAgent 式“同一轮多个项目并行，从同一记忆快照出发”。[^7_6][^7_7]
2. Runtime 能更容易控制并发、预算和结果合并。

广度探索时，一个典型 wave 可能长这样：

```text
wave-1
  - prerequisite: 识别导出按钮与其状态
  - variant: 在不同文件类型下尝试导出
  - contrast: 比较“按钮不可见”和“按钮可见但无权限”
  - stress: 连续导出、异常输入、边界文件
```


## 深度练习接口

针对 DRS，建议保留“上一轮失败诊断”入口。

```python
@dataclass
class CurriculumRefineRequest:
    target: LearningTarget
    previous_task: PracticeTask
    outcome: PracticeOutcome
    diagnosis: "FailureDiagnosis | SuccessFragilityDiagnosis | None"
    memory_snapshot: MemorySnapshot
    capability_map: CapabilityMap
    budget: ExplorationBudget
    policy: CurriculumPolicy
```

其中 `diagnosis` 很关键。深度练习不应只因为“失败了”就随机再试一题，而应根据失败类型生成更有信息量的练习。

例如诊断类型可包括：


| 类型 | 含义 |
| :-- | :-- |
| `missing_prerequisite` | 缺前置能力 |
| `wrong_causal_model` | 误解了环境因果 |
| `overgeneralized_rule` | 把局部经验误当通用规则 |
| `verification_gap` | 做了但无法证明做成 |
| `fragile_success` | 成功了但条件很脆弱 |

然后 `refine_after_result()` 应根据这些诊断选择不同 task family：

- `missing_prerequisite` → 多出 prerequisite 题。
- `wrong_causal_model` → 多出 contrast 题。
- `fragile_success` → 多出 stress 题。
- `verification_gap` → 多出可观测性/证据练习题。

这正符合 RSIAgent 的 DRS 精神：围绕暴露出的薄弱点顺序练习，而不是盲目重复原题。[^7_5][^7_3]

## 停止决策接口

课程生成器不只是出题器，也要是“何时停止学习”的建议者。

```python
@dataclass
class CurriculumStopRequest:
    target: LearningTarget
    mode: CurriculumMode
    memory_snapshot: MemorySnapshot
    outcomes: list[PracticeOutcome]
    coverage: "CoverageReport"
    budget_status: "BudgetStatus"
    risk_status: "RiskStatus"
```

```python
@dataclass
class CurriculumDecision:
    action: str   # continue | switch_mode | stop | escalate
    reason: str
    suggested_next_mode: str | None
```

停止原因可以包括：

- 覆盖收益趋于饱和。
- 高价值缺口已补齐。
- 剩余问题需要外部授权或环境。
- 风险过高。
- 预算耗尽。
- 已足够尝试目标任务。

这样 Runtime 就能把课程决策做成显式对象，而不是埋在 prompt 里。

## 建议的配套对象

为了让这个接口真正能落地，我建议再补 8 个配套类型。

### 1) 能力图

```python
@dataclass
class CapabilityMap:
    nodes: list["CapabilityNode"]
    edges: list["CapabilityDependency"]
    coverage_scores: dict[str, float]
```


### 2) 练习结果

```python
@dataclass
class PracticeOutcome:
    task_id: str
    status: str
    evidence_refs: list[str]
    memory_delta_refs: list[str]
    cost: dict
    diagnosis: dict | None
```


### 3) 学习信号

```python
@dataclass
class LearningSignal:
    signal_type: str   # discover | disambiguate | stress_verify | prerequisite_gain
    expected_information_gain: float
```


### 4) 课程理由

```python
@dataclass
class CurriculumReason:
    code: str
    message: str
    evidence_refs: list[str]
```

例如：

- `LOW_COVERAGE_ON_EXPORT_PATH`
- `HIGH_FAILURE_RATE_ON_AUTH_VARIANTS`
- `RULE_CONFLICT_DETECTED`
- `TARGET_DEPENDS_ON_MISSING_SKILL`


## 一个更完整的接口草案

下面给你一版更接近真实工程可用的接口骨架：

```python
class CurriculumGenerator(Protocol):
    def plan_wave(self, request: CurriculumRequest) -> CurriculumPlan: ...
    def refine_after_result(
        self,
        request: CurriculumRefineRequest
    ) -> CurriculumPlan | CurriculumDecision: ...
    def should_stop(self, request: CurriculumStopRequest) -> CurriculumDecision: ...


@dataclass
class CurriculumRequest:
    target: LearningTarget
    mode: str
    learner_state: dict
    memory_snapshot: MemorySnapshot
    capability_map: CapabilityMap
    recent_outcomes: list[PracticeOutcome]
    budget: ExplorationBudget
    policy: CurriculumPolicy


@dataclass
class CurriculumPlan:
    plan_id: str
    mode: str
    waves: list[PracticeWave]
    rationale: list[CurriculumReason]
    expected_learning_goals: list[LearningGoal]
    stop_hints: list[StopHint]
    next_review_after: str | None


@dataclass
class PracticeWave:
    wave_id: str
    shared_memory_snapshot_id: str
    tasks: list[PracticeTask]
    parallelism: int
    merge_policy: str


@dataclass
class PracticeTask:
    id: str
    family: str
    title: str
    objective: dict
    setup: dict
    constraints: dict
    acceptance_spec: AcceptanceSpec
    priority: float
    difficulty: float
    estimated_cost: CostEstimate
    expected_signal: LearningSignal
    depends_on: list[str]
    tags: list[str]
```

这套设计既能支撑：

- Voyager 风格的“面向新颖性与覆盖的自动课程”。[^7_2][^7_1]
- RSIAgent 风格的 “BRS 波次并行＋DRS 诊断驱动深练”。[^7_6][^7_3][^7_5]
- 你自己的 Core 中“可替换、可版本化、可审计”的课程控制面。


## 最小 MVP 版本

如果你现在只想先落一个 V1，我建议先固定下面 5 个接口：

1. `CurriculumGenerator`
2. `CurriculumRequest`
3. `CurriculumPlan`
4. `PracticeTask`
5. `CurriculumDecision`

然后把 V1 的课程任务族先限定为：

- `prerequisite`
- `variant`
- `contrast`
- `stress`
- `target_attempt`

停止决策先限定为：

- `continue`
- `switch_mode`
- `stop`

这已经足够支撑一个真正的自主课程系统，而不是只会输出下一题标题的轻量封装。

最关键的设计原则是：**课程生成器输出的不是“下一题建议”，而是一份带探索意图、执行约束、验收规范和停止提示的可运行学习计划。** 如果你愿意，下一步我可以直接继续把这套接口扩成一版 **Pydantic 模型定义 + JSON Schema 草案**。

<span style="display:none">[^7_10][^7_11][^7_12][^7_13][^7_14][^7_15][^7_16][^7_9]</span>

<div align="center">⁂</div>

[^7_1]: https://github.com/PatrickWiloak/ai-zero-to-hero/blob/main/papers/techniques/100-voyager/summary.md

[^7_2]: https://github.com/MineDojo/Voyager/tree/main

[^7_3]: https://hyper.ai/en/papers/2609.15364

[^7_4]: https://github.com/AetherLabsAI/RSIAgent

[^7_5]: https://github.com/AetherLabsAI/RSIAgent/blob/main/docs/PAPER.md

[^7_6]: https://github.com/AetherLabsAI/RSIAgent/blob/main/README.md

[^7_7]: https://agent-atlas.blackarcindustrial.com/papers/rsiagent-autonomous-exploration-for-recursive-self-improvement-in-new-environments/

[^7_8]: https://www.perplexity.ai/search/33065d4a-cc56-4a4c-a9a4-c9bb5fbf5ce3

[^7_9]: https://github.com/DeveloperHarris/Voyager

[^7_10]: https://github.com/AutoGpts/Voyager

[^7_11]: https://github.com/minedojo/voyager

[^7_12]: https://github.com/razor-ai/rsiagent

[^7_13]: https://github.com/Birfy/agentdescent/blob/main/docs/algo-voyager.md

[^7_14]: https://finance.sina.com.cn/tech/roll/2026-09-15/doc-inirwsee3855517.shtml

[^7_15]: https://skywork.ai/slide/en/voyager-ai-agent-library-lifelong-learning-2065049931407843328

[^7_16]: https://arxiv.org/html/2604.17460v2


---

# 聊聊如何为这个课程生成器实现默认的内存感知策略

默认的内存感知策略，核心不是“把所有记忆都喂给课程生成器”，而是让课程生成器**稳定地感知当前能力边界、失败热点、环境上下文与长期积累的经验，同时避免被噪声、过期经验和错误归纳带偏**。Voyager 的自动课程本质上就是依据当前状态、已完成/失败目标和探索进度，从“能力前沿”提出下一步；RSIAgent 则更进一步，把共享起始记忆、广度探索结果和深度练习反馈都纳入课程决策。[^8_1][^8_2][^8_3][^8_4]

同时，短期线程状态与长期知识库应分开管理；像 LangGraph 这样的系统也明确区分线程级状态/检查点与跨线程长期记忆，这对课程生成器尤其重要，因为“当前这一轮发生了什么”和“历史上已经学会了什么”属于不同证据层级。[^8_5][^8_6][^8_7]

## 设计目标

我建议默认策略满足四个目标：

- **少而准**：输入给课程生成器的记忆应是压缩过的决策视图，不是原始记忆全集。
- **先稳后新**：优先用已验证、未过期、适用范围明确的记忆决定课程方向。
- **前沿优先**：重点关注“快会了但还不稳”“刚失败过且高价值”“覆盖空白区”这三类区域。[^8_8][^8_3]
- **模式切换友好**：同一套记忆感知逻辑既能支持广度探索，也能支持深度练习。[^8_2][^8_1]


## 默认内存视图

建议课程生成器不要直接读取底层 MemoryStore，而是统一读取一个**课程专用记忆视图**，例如 `CurriculumMemoryView`。这个视图由 Runtime 在每轮课程生成前构建，来源可以很多，但格式应稳定。

```python
@dataclass
class CurriculumMemoryView:
    session_state: "SessionStateView"
    capability_frontier: "CapabilityFrontierView"
    gap_summary: "GapSummaryView"
    verified_rules: list["VerifiedRuleView"]
    active_skills: list["SkillView"]
    recent_failures: list["FailurePatternView"]
    recent_successes: list["SuccessPatternView"]
    coverage_map: "CoverageMapView"
    open_questions: list["OpenQuestionView"]
    memory_warnings: list["MemoryWarningView"]
```

这个设计的关键点是：**课程生成器读的是“面向课程决策整理后的记忆”，而不是直接做底层记忆检索器。**

## 课程默认读取哪些记忆

默认情况下，我建议分 6 层读取，按重要性排序。

### 1) 当前会话/当前波次状态

这是最短期、权重最高的一层。Voyager 的课程会参考当前库存、状态、最近完成目标；在你的通用基座里，对应的是当前环境状态、当前任务上下文、上一个波次结果和当前预算。[^8_3][^8_9]

建议包含：

- 当前模式：broad / deep / mixed。
- 当前目标与优先级。
- 最近 1 到 3 轮课程结果。
- 当前预算、风险、剩余时间。
- 当前环境上下文摘要。

这层信息的作用是：**防止课程生成器脱离眼前现实。**

### 2) 能力前沿视图

默认策略的中心应是“能力前沿”而不是“所有已知技能”。

可以定义：

```python
@dataclass
class CapabilityFrontierView:
    mastered: list[str]
    shaky: list[str]
    missing: list[str]
    blocked: list[str]
    newly_emerged: list[str]
```

其中：

- `mastered`：多次成功、回归稳定。
- `shaky`：偶尔成功但脆弱。
- `missing`：尚无可用路径。
- `blocked`：需要外部授权/环境。
- `newly_emerged`：最近才形成的新技能或规则。

课程默认应优先关注 `shaky` 和 `missing`，而不是继续练 `mastered`。[^8_8][^8_3]

### 3) 已验证规则与环境约束

RSIAgent 强调把可复用的环境知识写入持久记忆，供后续探索与下游任务复用。[^8_10][^8_1]

因此默认策略应读取：

- 已验证规则。
- 已知前置条件。
- 已知失败边界。
- 环境版本相关约束。
- 权限、速率、格式、顺序等操作条件。

但这层记忆只应用于**约束课程**，不应用于“锁死课程”。也就是说，它告诉生成器什么不该忘，但不能让生成器永远只围绕旧规则转。

### 4) 技能与可复用程序性记忆

Voyager 的 skill library 之所以关键，是因为课程不只是“学知识”，更是在能力边界上堆叠可复用行为模块。[^8_4][^8_3]

默认策略应读取：

- 可用技能名与用途。
- 技能依赖关系。
- 技能最近成功率。
- 技能适用环境。
- 技能最近是否退化。

课程生成器据此做两种判断：

1. 能否通过组合现有技能逼近新目标。
2. 是否需要围绕某个旧技能安排 `stress` 或 `regression` 任务。

### 5) 失败模式与未解决缺口

RSIAgent 的 DRS 阶段，本质上是围绕 target attempts 暴露出的 gap 和 fragile success 组织有针对性的练习。[^8_1][^8_2]

因此默认策略必须显式读取：

- 最近高频失败模式。
- 高价值未关闭缺口。
- 失败是否已被解释。
- 是否已有相反证据。

这层默认权重应在深度练习模式下最高。广度探索时只把它作为约束，不应让整个课程都围绕一个旧错误打转。

### 6) 覆盖图与开放问题

默认策略最后还应感知“哪里还没探索过”和“哪些结论冲突未解”。

建议维护两类对象：

- **CoverageMap**：某类能力、页面、对象、操作路径是否被探索过。
- **OpenQuestion**：例如“按钮灰掉是权限问题还是前置步骤未满足”。

这样课程生成器可以在 broad 模式下优先扩大覆盖，在 deep 模式下优先收敛冲突。

## 默认记忆选择顺序

为避免课程生成器变成大杂烩，我建议默认采用一个固定的记忆选择顺序：

```text
先看当前状态
→ 再看能力前沿
→ 再看最近失败/脆弱成功
→ 再看已验证规则与技能
→ 最后看覆盖盲区与开放问题
```

也就是说，课程生成器默认不是“语义检索记忆库”，而是“读取一个经过预编排的课程控制面”。

你可以把它理解为：底层记忆系统是数据库，课程生成器只看 materialized view。

## 默认优先级函数

建议给默认策略一个透明的打分函数，而不是完全交给 LLM 自由决定。

例如为每个候选练习方向计算：

$$
Score = w_v \cdot Value + w_u \cdot Uncertainty + w_f \cdot FailurePressure + w_c \cdot CoverageGap + w_n \cdot Novelty - w_k \cdot Cost - w_r \cdot Risk
$$

这个公式本身是设计建议，具体权重由产品策略决定。它体现了默认内存感知的核心思想：

- 业务价值高的优先。
- 不确定但可验证的优先。
- 最近频繁失败的优先。
- 覆盖空白适合 broad。
- 成本和风险过高的后置。

然后记忆层分别提供这些特征：


| 特征 | 默认从哪类记忆取 |
| :-- | :-- |
| `Value` | 当前目标、业务优先级 |
| `Uncertainty` | 规则冲突、脆弱成功、低置信技能 |
| `FailurePressure` | 最近失败模式、未闭合缺口 |
| `CoverageGap` | 覆盖图 |
| `Novelty` | 未见状态、未练路径、新环境对象 |
| `Cost` | 历史执行成本、环境限制 |
| `Risk` | 权限、不可逆动作、策略限制 |

## broad 模式的默认策略

在 broad 模式下，默认内存感知应尽量**少看细节，多看地图**。RSIAgent 的 BRS 是从共享起始记忆出发的多样化并行探索，重点是建立整体能力覆盖。[^8_2][^8_1]

所以 broad 模式下，默认课程视图建议突出：

- `coverage_map`
- `capability_frontier.missing`
- `newly_emerged`
- 高层环境约束
- 少量最近失败摘要

而弱化：

- 细粒度失败诊断。
- 单一技能的长历史。
- 大量历史轨迹文本。

可以用一个简单规则：

```text
broad 模式默认输入 =
  当前环境摘要
+ 覆盖空白 Top-K
+ 缺失能力 Top-K
+ 最近新增技能
+ 安全/权限边界
```

这样有利于生成 prerequisite、variant、contrast、stress 的多样化项目。[^8_11][^8_8]

## deep 模式的默认策略

在 deep 模式下，默认内存感知应**少看地图，多看局部病灶**。

建议 deep 模式优先读取：

- 最近一次 target attempt 的失败或脆弱成功。
- 相关技能的依赖链。
- 相关规则的适用条件。
- 相关开放问题和反例。
- 与本目标直接相关的前置知识。

一个典型 deep 视图可以是：

```text
目标：导出报告
最近失败：导出按钮可见但结果文件为空
相关技能：打开报表、设置筛选器、触发导出
已知规则：未设置日期范围时导出为空
未解决问题：空文件来自日期范围缺失还是权限限制
建议关注：contrast + prerequisite
```

这正是把记忆从“检索背景信息”变成“课程定位器”。

## 默认过滤规则

为了防止记忆污染课程生成，我建议默认加 6 条过滤规则。

### 1) 只优先使用 VERIFIED / ACTIVE 记忆

候选记忆、冲突记忆和过期记忆不应直接作为课程生成主依据，而应只作为“不确定性来源”。

### 2) 过期或环境版本不匹配的降权

如果一条规则针对旧 UI、旧 API 版本或旧权限模型，默认降权或排除。

### 3) 近重复任务抑制

若一个任务家族在最近 N 轮已高频出现且没有新增信息增益，默认抑制。

### 4) 成功过多的领域降温

若某项技能已经稳定成功，默认不再生成同类 prerequisite 练习，除非在 regression 模式下。

### 5) 单一失败不直接升级为系统性规律

一次失败默认进入 recent failure，而不是生成“长期缺口”或“环境规则”。

### 6) 冲突记忆触发 contrast

如果两条已发布记忆对同一条件给出不一致结论，默认不选边站，而是触发 contrast 课程。

## 默认聚合器

为了让课程生成器实现简洁，我建议在它前面加一个内存聚合器：

```python
class CurriculumMemoryAssembler(Protocol):
    def assemble(
        self,
        target: LearningTarget,
        mode: str,
        memory_snapshot: MemorySnapshot,
        recent_outcomes: list[PracticeOutcome],
        budget: ExplorationBudget
    ) -> CurriculumMemoryView: ...
```

默认实现内部可拆成几个子步骤：

1. 读取 session/checkpoint 级状态。[^8_6][^8_7]
2. 读取长期记忆命名空间，如 skills、rules、gaps、coverage。[^8_12][^8_6]
3. 合并最近 outcomes 与 verifier 诊断。
4. 做去重、降噪、过期过滤。
5. 生成 capability frontier 与 gap summary。
6. 按 mode 生成不同课程视图。

这样课程生成器的主逻辑就可以专注于“出什么题”，而不是“怎么从十几种存储里翻数据”。

## 默认命名空间建议

如果你要做一个可扩展的基座，我建议默认把长期记忆至少拆成这些命名空间：


| 命名空间 | 内容 |
| :-- | :-- |
| `skills/*` | 技能、程序性记忆、执行模板 |
| `rules/*` | 已验证规则、约束、前置条件 |
| `gaps/*` | 缺口与未解决问题 |
| `coverage/*` | 覆盖图、探索地图 |
| `failures/*` | 失败模式与脆弱成功摘要 |
| `profiles/*` | 环境画像、应用画像、用户画像 |
| `releases/*` | 与版本绑定的记忆快照索引 |

这样做的好处是，你可以让第三方用户扩展新的记忆类型，同时不破坏默认课程聚合器。

## 默认策略的可扩展点

即使你提供默认内存感知策略，也应保留这些可替换点：


| 扩展点 | 默认实现 | 用户可替换什么 |
| :-- | :-- | :-- |
| `FrontierBuilder` | 基于技能成功率和缺口状态 | 自己的能力成熟度模型 |
| `FailureSummarizer` | 最近失败聚合 | 行业特定故障分类 |
| `CoverageBuilder` | 基于任务族/对象/路径统计 | 自定义覆盖维度 |
| `MemoryRanker` | 按验证状态、时效、相关性排序 | 自定义检索与排序 |
| `ModeLens` | broad/deep 不同视图 | 新的课程风格 |
| `TaskSuppressionPolicy` | 防重复、控噪 | 更激进或更保守的课程节奏 |

这保证“默认策略可用”，同时“高级用户可深入定制”。

## 一个建议的默认实现轮廓

我会建议默认策略大致按下面方式工作：

```text
输入目标与模式
  → 读取会话级短期状态
  → 读取长期 skills / rules / gaps / coverage / failures
  → 过滤未验证、过期、不适用记忆
  → 生成 capability frontier
  → 生成 gap summary
  → 根据 broad/deep 套不同视图模板
  → 对候选学习方向打分
  → 输出课程生成器使用的 CurriculumMemoryView
```

如果只浓缩成一句话，默认的内存感知策略应是：

> **让课程生成器始终站在“当前能力前沿”上出题，而不是站在“历史记忆堆”上回忆。**

这也是为什么默认实现里，最核心的不是向量检索本身，而是 **frontier、gap、coverage、recent outcome 这四个中间视图**。它们把底层复杂记忆压缩成课程决策真正需要的东西。[^8_5][^8_3][^8_1]

下一步最值得展开的是两部分之一：**`CurriculumMemoryAssembler` 的详细接口设计**，或者 **默认 `capability_frontier + gap_summary` 的具体计算规则**。

<span style="display:none">[^8_13][^8_14][^8_15][^8_16][^8_17]</span>

<div align="center">⁂</div>

[^8_1]: https://arxiv.org/html/2609.15364

[^8_2]: https://github.com/AetherLabsAI/RSIAgent

[^8_3]: https://gaos6e.github.io/AI-Agent-Engineer/en/landmarks/05-voyager-automatic-curriculum-and-skill-library

[^8_4]: https://voyager.minedojo.org/

[^8_5]: https://markaicode.com/langgraph-memory-short-term-long-term-storage/

[^8_6]: https://docs.langchain.com/oss/python/concepts/memory

[^8_7]: https://docs.langchain.com/oss/python/langgraph/add-memory

[^8_8]: https://github.com/curiositech/windags-skills/blob/main/skills/wang-2023-voyager/references/automatic-curriculum-as-frontier-discovery.md

[^8_9]: https://github.com/PatrickWiloak/ai-zero-to-hero/blob/main/papers/techniques/100-voyager/summary.md

[^8_10]: https://github.com/razor-ai/rsiagent/tree/main/

[^8_11]: https://agent-atlas.blackarcindustrial.com/papers/rsiagent-autonomous-exploration-for-recursive-self-improvement-in-new-environments/

[^8_12]: https://deepwiki.com/langchain-ai/langchain-academy/8-memory-systems

[^8_13]: https://github.com/MineDojo/Voyager/blob/main/voyager/prompts/curriculum.txt

[^8_14]: https://machinelearningplus.com/gen-ai/langgraph-memory-systems-short-long-term-conversation/

[^8_15]: https://systempromptindex.ai/gallery/voyager

[^8_16]: https://svgoudar.github.io/Learn-LangGraph/langgraph/2-state-data-management/19-memory-store.html

[^8_17]: https://deepwiki.com/langchain-ai/langgraph-101/4.4-memory-management


---

# 看看 CurriculumMemoryAssembler 的详细接口设计

可以。`CurriculumMemoryAssembler` 最好不要只是“查记忆然后拼一下”，而应被设计成**课程生成器前的专用控制面聚合器**：它从线程级短期状态、跨线程长期记忆、最近结果、环境资源与运行策略中，构造出一个稳定、低噪声、面向课程决策的 `CurriculumMemoryView`。这和 LangGraph 把线程级 checkpoint 与跨线程 store 分开持久化的思路一致，也符合你现在这套 Core 里“状态机/记忆/课程生成分层”的方向。[^9_1][^9_2][^9_3]

另外，A2A 里的 Task/Artifact 很适合作为“最近执行结果与产物”的上游输入，而 MCP 的 resources / prompts / tools 则适合作为环境上下文来源；但它们本身不负责长期记忆建模，所以汇聚和裁剪仍应由你自己的 Assembler 完成。[^9_4][^9_5][^9_6][^9_7]

## 职责边界

我建议先把 `CurriculumMemoryAssembler` 的职责说死，不然后面一定膨胀：

- 读取多源记忆与运行态。
- 过滤、归一化、去噪、冲突标记。
- 生成课程专用中间视图。
- 不直接出课程。
- 不直接写长期记忆。
- 不负责最终验证，只消费验证结果。

也就是说，它的位置应是：

```text
Checkpointer / Store / Artifact Log / Runtime State / MCP Resources
  → CurriculumMemoryAssembler
  → CurriculumMemoryView
  → CurriculumGenerator
  → CurriculumPlan
```

这样课程生成器永远读“课程视图”，而不是自己深入各种存储。

## 顶层接口

我建议顶层接口至少拆成 4 个方法，而不是一个 `assemble()` 包打天下。

```python
class CurriculumMemoryAssembler(Protocol):
    def assemble(
        self,
        request: "CurriculumMemoryRequest"
    ) -> "CurriculumMemoryView": ...

    def preview(
        self,
        request: "CurriculumMemoryRequest"
    ) -> "CurriculumMemoryPreview": ...

    def explain(
        self,
        view: "CurriculumMemoryView"
    ) -> "CurriculumMemoryExplanation": ...

    def validate(
        self,
        view: "CurriculumMemoryView"
    ) -> "CurriculumMemoryValidationReport": ...
```

这样拆的意义是：

- `assemble()` 真正产出课程视图。
- `preview()` 用于调试和可视化，先看会召回哪些记忆。
- `explain()` 给出“为什么这些记忆被选入/降权/排除”的理由。
- `validate()` 检查这个视图是否缺关键字段、是否噪声过大、是否冲突过多。

课程生成器一旦复杂，没有 `preview/explain/validate` 会很难调。

## 输入接口

### 1) 主请求对象

```python
@dataclass
class CurriculumMemoryRequest:
    target: "LearningTarget"
    mode: "CurriculumMode"                  # broad | deep | mixed
    thread_ref: "ThreadRef | None"
    task_ref: "TaskRef | None"
    memory_snapshot_ref: "MemorySnapshotRef | None"
    recent_outcomes: list["PracticeOutcomeRef"]
    learner_scope: "LearnerScope"
    budget: "ExplorationBudget"
    policy: "CurriculumMemoryPolicy"
    options: "CurriculumMemoryOptions"
```

这个请求对象要同时兼容：

- 当前线程中的连续课程。
- 跨线程长期课程。
- 离线回放某次课程决策。
- 用冻结快照复现实验。

LangGraph 文档强调 checkpointer 保存线程状态，store 保存跨线程长期信息；你这里就应显式同时支持 `thread_ref` 和 `memory_snapshot_ref` 两种入口。[^9_8][^9_1]

### 2) 学习者范围

```python
@dataclass
class LearnerScope:
    learner_id: str
    team_id: str | None
    environment_id: str | None
    application_id: str | None
    release_id: str | None
```

为什么要有这个？因为长期记忆必须带 namespace 和适用边界，否则不同应用、不同版本、不同 Agent 的记忆会互相污染。[^9_2][^9_9]

### 3) 组装选项

```python
@dataclass
class CurriculumMemoryOptions:
    max_items_per_section: int = 12
    include_artifacts: bool = True
    include_open_questions: bool = True
    include_conflicts: bool = True
    include_blocked_capabilities: bool = True
    max_token_budget_hint: int | None = None
    strict_applicability_filter: bool = True
    recency_window_hours: int = 168
```

这让你可以控制视图规模，不至于把“所有相关记忆”都塞进去。

## 输出接口

### 1) 课程视图主体

```python
@dataclass
class CurriculumMemoryView:
    meta: "CurriculumMemoryMeta"
    session_state: "SessionStateView"
    frontier: "CapabilityFrontierView"
    gaps: "GapSummaryView"
    rules: list["RuleView"]
    skills: list["SkillView"]
    failures: list["FailurePatternView"]
    successes: list["SuccessPatternView"]
    coverage: "CoverageMapView"
    open_questions: list["OpenQuestionView"]
    blocked: list["BlockedAreaView"]
    signals: list["CurriculumSignal"]
    warnings: list["MemoryWarningView"]
    provenance: list["MemoryProvenanceEntry"]
```

这个结构的重点不是字段多，而是每一栏都有明确课程用途：

- `session_state`：眼前发生了什么。
- `frontier/gaps`：能力前沿在哪里。
- `rules/skills`：已有资产是什么。
- `failures/successes`：最近应该避开什么、巩固什么。
- `coverage/open_questions`：下一轮最值得探索什么。
- `signals`：给课程生成器的压缩提示。
- `provenance`：可追溯这份视图由哪些记忆构成。


### 2) 元信息

```python
@dataclass
class CurriculumMemoryMeta:
    assembled_at: str
    assembler_version: str
    mode: str
    target_id: str
    thread_id: str | None
    memory_snapshot_id: str | None
    token_estimate: int
    source_counts: dict[str, int]
```

这里建议始终记录 `assembler_version` 和 `memory_snapshot_id`，因为后续调试课程行为时，你会非常需要知道“当时到底看了哪一版记忆”。

## 子视图接口

### 1) SessionStateView

```python
@dataclass
class SessionStateView:
    current_goal_summary: str
    current_environment_summary: str
    active_mode: str
    recent_wave_ids: list[str]
    budget_status: "BudgetStatusView"
    risk_status: "RiskStatusView"
    latest_artifacts: list["ArtifactRef"]
```

线程级状态最适合来自 checkpointer 或当前任务状态；LangGraph 里短期状态就是图状态的一部分，并在步骤间更新。[^9_10][^9_2]

### 2) CapabilityFrontierView

```python
@dataclass
class CapabilityFrontierView:
    mastered: list["CapabilityStatus"]
    shaky: list["CapabilityStatus"]
    missing: list["CapabilityStatus"]
    blocked: list["CapabilityStatus"]
    newly_emerged: list["CapabilityStatus"]
```

```python
@dataclass
class CapabilityStatus:
    capability_id: str
    label: str
    confidence: float
    evidence_count: int
    last_seen_at: str | None
    applicability: dict
```

这是课程视图最重要的一块，因为课程生成器默认不该围绕“全部技能”，而该围绕“边界上的技能”。

### 3) GapSummaryView

```python
@dataclass
class GapSummaryView:
    high_value_gaps: list["GapView"]
    recurring_gaps: list["GapView"]
    unresolved_gaps: list["GapView"]
    newly_detected_gaps: list["GapView"]
```

```python
@dataclass
class GapView:
    gap_id: str
    title: str
    gap_type: str
    severity: float
    recurrence: int
    related_capabilities: list[str]
    likely_causes: list[str]
    blocking_target_ids: list[str]
```

深练模式会非常依赖这一层。

### 4) RuleView / SkillView

```python
@dataclass
class RuleView:
    rule_id: str
    statement: str
    verification_status: str
    applicability: dict
    confidence: float
    source_refs: list[str]
    superseded_by: str | None
```

```python
@dataclass
class SkillView:
    skill_id: str
    name: str
    description: str
    success_rate: float
    fragility_score: float
    dependencies: list[str]
    applicability: dict
```

Voyager 的 skill library 强调可复用行为单元及其检索；这里你不一定照搬实现，但 `SkillView` 必须提供“能否复用”和“是否脆弱”这两类课程信号。[^9_11][^9_12]

### 5) FailurePatternView / SuccessPatternView

```python
@dataclass
class FailurePatternView:
    pattern_id: str
    summary: str
    recurrence: int
    last_seen_at: str
    related_capabilities: list[str]
    suspected_causes: list[str]
    evidence_refs: list[str]
```

```python
@dataclass
class SuccessPatternView:
    pattern_id: str
    summary: str
    recurrence: int
    fragility_score: float
    related_capabilities: list[str]
    evidence_refs: list[str]
```

注意成功也要进视图，因为“脆弱成功”往往比明确失败更值得被课程生成器关注。

### 6) CoverageMapView

```python
@dataclass
class CoverageMapView:
    dimensions: list[str]              # object, path, tool, state, permission...
    covered_cells: list["CoverageCell"]
    sparse_regions: list["CoverageCell"]
    hot_regions: list["CoverageCell"]
```

```python
@dataclass
class CoverageCell:
    key: str
    label: str
    coverage_score: float
    recent_attempts: int
    success_rate: float | None
```

broad 模式下，这一层会被高权重使用。

### 7) OpenQuestionView / BlockedAreaView

```python
@dataclass
class OpenQuestionView:
    question_id: str
    question: str
    related_capabilities: list[str]
    conflict_refs: list[str]
    priority: float
```

```python
@dataclass
class BlockedAreaView:
    blocked_id: str
    reason_type: str          # permission | environment | dependency | policy
    summary: str
    unblock_conditions: list[str]
    affected_capabilities: list[str]
```

这样课程生成器就能区分“值得继续探索的问题”和“应该暂停等待外部解锁的问题”。

## 预览 / 解释 / 校验接口

### 1) Preview

```python
@dataclass
class CurriculumMemoryPreview:
    selected_memory_ids: list[str]
    dropped_memory_ids: list[str]
    section_estimates: dict[str, int]
    warnings: list[str]
```

这适合开发时先看“视图组装前后发生了什么”。

### 2) Explain

```python
@dataclass
class CurriculumMemoryExplanation:
    section_explanations: list["SectionExplanation"]
    ranking_factors: dict[str, float]
```

```python
@dataclass
class SectionExplanation:
    section: str
    included_because: list[str]
    excluded_because: list[str]
```

比如可以输出：

- `failures` 被保留，因为最近 24 小时 recurrence 高且与目标直接相关。
- 某条旧规则被降权，因为 release_id 不匹配。


### 3) Validate

```python
@dataclass
class CurriculumMemoryValidationReport:
    valid: bool
    missing_sections: list[str]
    conflict_count: int
    stale_ratio: float
    noise_ratio: float
    token_estimate: int
    recommendations: list[str]
```

一个常见问题是视图本身质量不够；比如 80% 都是过期记忆，那课程再聪明也没用。

## 依赖接口

`CurriculumMemoryAssembler` 不应直接绑死某种数据库。建议它依赖下面这些抽象。

### 1) 短期状态读取

```python
class SessionStateReader(Protocol):
    def read_thread_state(self, thread_ref: ThreadRef) -> dict: ...
    def read_task_state(self, task_ref: TaskRef) -> dict: ...
```

这和 LangGraph 的 checkpointer / thread 概念天然对应。[^9_1][^9_10]

### 2) 长期记忆读取

```python
class LongTermMemoryReader(Protocol):
    def query_namespace(
        self,
        scope: LearnerScope,
        namespace: str,
        query: dict
    ) -> list[dict]: ...
```

LangGraph 的 store 是跨线程、命名空间化的长期数据存储；你的抽象也应保留 namespace 概念。[^9_3][^9_13][^9_2]

### 3) 结果与产物读取

```python
class OutcomeReader(Protocol):
    def read_recent_outcomes(
        self,
        refs: list[PracticeOutcomeRef]
    ) -> list["PracticeOutcome"]: ...

class ArtifactReader(Protocol):
    def read_artifacts(
        self,
        task_ref: TaskRef | None,
        limit: int
    ) -> list["ArtifactRef"]: ...
```

A2A 任务输出中的 artifacts 特别适合进入 session_state 或 evidence refs。[^9_6][^9_4]

### 4) 外部环境上下文读取

```python
class ContextResourceReader(Protocol):
    def read_resources(
        self,
        target: LearningTarget,
        scope: LearnerScope
    ) -> list["ContextResource"]: ...
```

MCP 里的 resources 和 prompts 可以成为环境上下文输入源，但要先经过归一化，不要直接塞入课程视图。[^9_5][^9_14]

## 组装流水线接口

为了可维护，我建议把 `assemble()` 再拆成内部流水线阶段，每个阶段都是可替换组件。

```python
class MemoryFetchStage(Protocol):
    def run(self, request: CurriculumMemoryRequest) -> "FetchedMemoryBundle": ...

class MemoryNormalizeStage(Protocol):
    def run(self, bundle: "FetchedMemoryBundle") -> "NormalizedMemoryBundle": ...

class MemoryFilterStage(Protocol):
    def run(
        self,
        bundle: "NormalizedMemoryBundle",
        policy: "CurriculumMemoryPolicy"
    ) -> "FilteredMemoryBundle": ...

class MemoryProjectStage(Protocol):
    def run(
        self,
        bundle: "FilteredMemoryBundle",
        mode: str
    ) -> CurriculumMemoryView: ...
```

这四段分别做：

1. 拉取。
2. 归一化。
3. 过滤与排序。
4. 投影为课程视图。

## 默认策略钩子

默认实现中最值得开放给用户替换的是这些钩子：


| 钩子 | 默认职责 |
| :-- | :-- |
| `ApplicabilityFilter` | 过滤环境/版本/权限不匹配记忆 |
| `RecencyRanker` | 最近记忆加权 |
| `VerificationRanker` | 优先 VERIFIED / ACTIVE |
| `ConflictDetector` | 发现规则/技能冲突 |
| `FrontierBuilder` | 构造 mastered/shaky/missing |
| `GapBuilder` | 归纳缺口 |
| `CoverageBuilder` | 生成覆盖图 |
| `SignalSynthesizer` | 从视图合成课程信号 |

最后这个 `SignalSynthesizer` 很关键，因为课程生成器不一定想自己读所有 section。它可以产出高度压缩的调度信号。

```python
@dataclass
class CurriculumSignal:
    code: str
    score: float
    summary: str
    related_refs: list[str]
```

例如：

- `FOCUS_PREREQUISITE_EXPORT_SETUP`
- `EXPAND_COVERAGE_PERMISSION_VARIANTS`
- `RUN_CONTRAST_ON_EMPTY_OUTPUT_CAUSES`


## 一个接近实现的总草案

```python
class CurriculumMemoryAssembler(Protocol):
    def assemble(self, request: CurriculumMemoryRequest) -> CurriculumMemoryView: ...
    def preview(self, request: CurriculumMemoryRequest) -> CurriculumMemoryPreview: ...
    def explain(self, view: CurriculumMemoryView) -> CurriculumMemoryExplanation: ...
    def validate(self, view: CurriculumMemoryView) -> CurriculumMemoryValidationReport: ...


@dataclass
class CurriculumMemoryRequest:
    target: LearningTarget
    mode: str
    thread_ref: ThreadRef | None
    task_ref: TaskRef | None
    memory_snapshot_ref: MemorySnapshotRef | None
    recent_outcomes: list[PracticeOutcomeRef]
    learner_scope: LearnerScope
    budget: ExplorationBudget
    policy: CurriculumMemoryPolicy
    options: CurriculumMemoryOptions


@dataclass
class CurriculumMemoryView:
    meta: CurriculumMemoryMeta
    session_state: SessionStateView
    frontier: CapabilityFrontierView
    gaps: GapSummaryView
    rules: list[RuleView]
    skills: list[SkillView]
    failures: list[FailurePatternView]
    successes: list[SuccessPatternView]
    coverage: CoverageMapView
    open_questions: list[OpenQuestionView]
    blocked: list[BlockedAreaView]
    signals: list[CurriculumSignal]
    warnings: list[MemoryWarningView]
    provenance: list[MemoryProvenanceEntry]
```


## 推荐的最小 MVP

如果你现在先做 V1，我建议先只落这几部分：

1. `assemble()`
2. `CurriculumMemoryRequest`
3. `CurriculumMemoryView`
4. `SessionStateReader`
5. `LongTermMemoryReader`
6. `FrontierBuilder`
7. `GapBuilder`
8. `SignalSynthesizer`

并且 V1 的视图先只保留：

- `session_state`
- `frontier`
- `gaps`
- `rules`
- `skills`
- `failures`
- `signals`

这已经足够驱动一个像样的课程生成器，而且不会一上来把系统做得过重。

如果把它压成一句话：**`CurriculumMemoryAssembler` 的本质不是“记忆检索器”，而是“把多源状态和长期经验压缩成课程可消费决策视图的投影器”。**

<span style="display:none">[^9_15][^9_16][^9_17]</span>

<div align="center">⁂</div>

[^9_1]: https://docs.langchain.com/oss/python/langgraph/persistence.md

[^9_2]: https://docs.langchain.com/oss/python/concepts/memory

[^9_3]: https://docs.langchain.com/oss/python/langgraph/stores

[^9_4]: https://a2a-protocol.org/pr-849/specification/

[^9_5]: https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture

[^9_6]: https://agent2agent.info/docs/concepts/

[^9_7]: https://www.wwt.com/blog/agent-2-agent-protocol-a2a-a-deep-dive

[^9_8]: https://docs.langchain.com/oss/python/langgraph/persistence

[^9_9]: https://docs.langchain.com/oss/python/concepts/memory.md

[^9_10]: https://docs.langchain.com/oss/javascript/langgraph/checkpointers

[^9_11]: https://voyager.minedojo.org/

[^9_12]: https://gaos6e.github.io/AI-Agent-Engineer/en/landmarks/05-voyager-automatic-curriculum-and-skill-library

[^9_13]: https://docs.langchain.com/oss/javascript/langgraph/stores

[^9_14]: https://www.tricentis.com/learn/mcp-prompts

[^9_15]: https://docs.langchain.com/oss/javascript/langgraph/add-memory

[^9_16]: https://hindsight.vectorize.io/blog/2026/03/24/langgraph-longterm-memory

[^9_17]: https://cloud.google.com/discover/what-is-model-context-protocol


---

# 看看 CurriculumMemoryView 的具体数据结构定义

Answer skipped.


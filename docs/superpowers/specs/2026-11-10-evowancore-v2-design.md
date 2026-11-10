# EvoWanCore V2 设计文档（全深版）

- 日期：2026-11-10
- 状态：待评审（builder check）→ 待用户确认
- 前置：V1 已合并 master（实验结论见 `docs/experiments/RESULTS.md`：探索 +16.7pp，extract 维度两臂 0.33 均未学会，错误晋级率 0）
- 标注：**[已确认]** = 用户在澄清对话拍板；**[我的裁量]** = 待用户复核

---

## 1. 意图与动机

V2 命题：**让系统学会它没学会的东西，并打开双向生态接口。**

三个证据驱动的靶点：
1. **extract 0.33**（轮 2 实验）：双栏陷阱两臂都没学会——课程系统不会针对"练了没过"的维度自我纠正
2. **演化轨道未接入主回路**（V1 评审 I1 修复的深层遗留）：EvolutionController 建好了但发布路径仍是工作台真值门，reflect 是确定性函数——真 LLM 反思没有通用安全门禁
3. **多模态只到资产层**：图片存在于证据链但模型从不读图；MCP 契约预留未实装

北极星指标：**复跑主实验，extract 维度可测量提升（0.33 → ≥0.67），错误经验晋级率保持 0。** [已确认]

## 2. 目标 / 非目标 / 反目标

### 目标

**A 地基巩固**（~6 任务）
1. 沙箱网络隔离（按技能声明放行；平台分支实现）
2. runtime `enforce_budget` 接线（逐动作预算门，超限走 FAILED）
3. resume 续跑剩余步骤（checkpoint 携带执行上下文，恢复后继续循环而非仅对账收口）
4. 清 M1（环境版本参数化出 Core + 记忆盖戳 environment_version）、M8（公有 API 替代私有访问：runtime 事件读取、registry 重绑定、build_app 注入 verifier）、M10（演化态持久化到 SQLite + NS_RELEASES；空契约测试 fail）

**B 闭环智能化**（~10 任务，核心）
5. **真 LLM reflector**：轨迹→候选经验的反思走 gateway；产出必须过**通用评测门禁**——`EvolutionController.evaluate`（小批次父子对比 + 回归检查）通过才 `publish`；工作台真值表**降级为审计工具**，不再承担发布 [已确认]
6. **gap→contrast 课程闭环**：GapLedger 的未关闭缺口驱动课程——含未通过维度的针对性 contrast/variant 练习；波次校验：存在 open gap 时波内必须含针对该 gap 的任务
7. 审计强模型复核：`audit_promoted(reviewer=…)` 接 gateway（默认关，可开）
8. **复跑主实验验证 extract 提升**

**C-VLM 图文**（~8 任务，T24 模式 [已确认]）
9. 工作台感知化：字段值只存在于渲染 PNG（多版式渲染增强：表格网格/双栏分块/扫描噪声）；`extract_fields` 变体返回图片引用，模型必须读图取值
10. **第四隐藏约束（感知陷阱）**：低对比度扫描页（contrast < 0.5）VLM 读不准——必须先 `convert_format` 提升对比度；FakeVLM 按对比度模拟错误率，真模型即插即跑
11. 感知化语料的主实验接入（image-based extract 任务进保留集）

**C-MCP 双向**（~8 任务）
12. **client**：外部 MCP server 工具经 `ProtocolAdapter.import_capabilities()` 进统一能力目录；接真实 server（filesystem）做参考验收 [已确认 client 优先]
13. **server**：Core 能力经 MCP 暴露（官方 SDK，stdio）；**权限门控**——只暴露 `side_effect_level != "irreversible"` 且无 auth_scope 要求（或在白名单）的能力，调用复用 PolicyEngine.authorize [已确认]

### 非目标（V3+）

代码级演化（DGM）、CUA、服务化+TS SDK、多 Agent 编排（CompositionPolicy 实现）、A2A、记忆物理清除、Realtime 真供应商接入、VLM 真模型实跑（key 后补即跑，不阻塞交付）

### 反目标（继承 V1 全部，新增）

- reflector 产出无门禁直发（I1 教训的泛化）
- 感知任务的判分依赖模型自述（必须程序化对照渲染真值）
- MCP server 暴露不可逆能力

## 3. 约束

- 继承 V1 全局约束（Python 3.11+/asyncio/Pydantic v2/Protocol/SQLite/子进程沙箱/每任务 TDD）
- VLM：FakeVLM 钉死全部行为，真 key 后补；`ModelCapabilities` 声明 IMAGE 输入，路由按能力协商
- MCP：官方 `mcp` python SDK（MIT/Apache），stdio transport 优先；SDK 依赖进 `agent-adapters`
- 实验成本：DeepSeek flash，单轮 ≤ 15 分钟量级；VLM 实验待 key
- 沙箱网络隔离按平台分支（macOS `sandbox-exec` / Linux `unshare -n` 包装器），失败降级为文档化限制并记录

## 4. 方案选择（Approaches）

- **V2 深度**：方案一（B深C薄）/ 方案二（C深B薄）/ **全深版** ✅ [已确认，用户定案]
- **B 门禁路径**：工作台真值门（环境特定，无法泛化）/ **通用评测门**（EvolutionController 父子对比+回归门，环境无关）✅ [已确认]
- **VLM 场景载体**：新建场景 / **复用工作台**（管道、验收、实验 harness 全复用，感知陷阱成为第四隐藏约束）✅ [已确认]
- **MCP 方向**：client 单向 / **双向**（server 端逼出权限模型实战）✅ [已确认]

## 5. 架构增量

### 5.1 B 块：闭环智能化

```text
练习 outcome + 证据
  → LLMReflector(gateway).reflect(task, outcome)      # 真 LLM 反思，产出候选
  → MemoryWriter.propose/validate                      # 主张门禁（继承）
  → EvolutionController.evaluate(mini_batch, runner,   # 通用评测门：父子对比+回归
                                  regression_scenarios)
  → publish（通过）/ 拒绝记事件（未通过，候选保持 CANDIDATE）
审计：statement_matches_truth 仅用于 audit 报告，不再 gate
```

- `LLMReflector` 实现 V1 的 reflector 注入协议（同步签名、返回 proposals 列表），内部走 gateway + 结构化输出（复用降级链）
- 课程闭环：`CurriculumMemoryAssembler` 已输出 `gaps` 与 signals；增强 `LLMCurriculumGenerator.plan_wave` 提示（open gap 必须有对应任务）+ **可校验约束**：存在 open gap 时波内任务的目标维度必须覆盖 gap 的 affected dimension（不满足则重试/报错）
- `_diagnose` 对 extract 类失败输出 `wrong_causal_model` → contrast 族（映射已存在，补场景化诊断）

### 5.2 C-VLM：工作台感知化

- `generator` v2：渲染层增强（表格/双栏/噪声/对比度参数化）；`doc["fields"]` 值不进工具通道——`extract_fields_from_image(doc)` 返回 `{"image_ref": asset_id, "pages": [...]}`；真值只在验收脚本与渲染 PNG 中
- **FakeVLMAdapter**：声明 IMAGE 输入；按页面对比度模拟读图错误率（contrast≥0.5 全对，<0.5 概率性错值；convert 后对比度→1.0 全对）——这条行为就是第四隐藏约束的可测载体
- `ModelLoopPolicy` 消息构造支持 image content parts（`InferRequest.messages` 为自由 dict，无需改动契约；路由按 `inputs={IMAGE}` 命中 VLM 适配器）
- 主实验：感知化语料生成 image-based extract 任务进保留集；VLM 实验待 key，FakeVLM 下先验证管道与门禁

### 5.3 C-MCP：双向接入

- **client**：`MCPClientAdapter`（`ProtocolAdapter` 实现）——mcp SDK ClientSession（stdio）启动外部 server，`list_tools` → `CapabilityDescriptor`（input_schema 映射、side_effect_level 默认 `read`、可声明覆盖）；`call_tool` 绑定为 executor handler。参考验收：filesystem server 的 read/list 工具 roundtrip
- **server**：`MCPServerExporter`——Core registry 中**可暴露子集**（非 irreversible、无 auth_scope）→ MCP tools；每个调用构造 `ActionRequest` 走 `PolicyEngine.authorize` + `CapabilityExecutor`（复用全部门禁与证据链）
- 连接生命周期：lazy connect + 空闲超时；stdoi subprocess 归入现有进程管理纪律

### 5.4 A 块：地基

- 网络隔离：`SkillSandbox(network: "deny"|"allow", …)` → 平台包装器（macOS sandbox-exec profile / Linux unshare）；失败（平台不支持）→ 明确报错并文档化
- 预算接线：AgentRuntime 构造收 `budget_meter`；每 action 后 `enforce_budget`，超限 → run FAILED（reason=budget）
- resume 续跑：checkpoint 增量保存 `context`（history/pending）；`resume` 重建 context 后继续主循环；已在途动作仍走 OUTCOME_UNKNOWN 对账
- M1/M8/M10 按 V1 评审记录原文实施

## 6. 实验与验收

1. **主实验 v3**（B 完成后）：难度 0.8 感知化语料、3+ 新种子、extract 维度提升 ≥ 0.67 为通过线；错误晋级率 = 0 红线
2. **门禁回归**：reflector 产出错误陈述时评测门拒绝（RED→GREEN 测试）
3. **MCP 参考验收**：filesystem server roundtrip（client）；server 端外部调用不可逆能力被拒
4. **感知化验收**：FakeVLM 在低对比度页出错、convert 后全对（约束可学性）；image-based extract 任务程序化判分
5. 全量 154+ 测试保持绿；每块 TDD

## 7. 留给实现者

- reflector/课程提示词文案；渲染细节（字体/网格/噪声算法）；FakeVLM 错误率曲线；MCP schema 映射边角；sandbox-exec profile 内容；实验种子与预算微调

## 8. 待复核裁量清单

| # | 裁量 | 默认 |
|---|---|---|
| 1 | MCP SDK 引入位置 | agent-adapters，仅此包依赖 mcp |
| 2 | 参考验收的 MCP server | 官方 filesystem server（stdio） |
| 3 | 网络隔离平台分支降级策略 | 不支持平台显式报错，不静默跳过 |
| 4 | 感知陷阱阈值 | contrast < 0.5 不可靠，convert 后 → 1.0 |
| 5 | VLM 路由 | inputs={IMAGE} 显式声明；infer 通道批处理（非 realtime） |
| 6 | server 暴露白名单 | 默认只读能力；白名单配置可扩 |

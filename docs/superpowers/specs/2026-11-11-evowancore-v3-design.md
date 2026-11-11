# EvoWanCore V3 设计文档（真模型达标 + 技能演化）

- 日期：2026-11-11
- 状态：待评审（builder check）→ 待用户确认
- 前置：V2 已合并 master（北极星 FakeVLM 口径 extract 1.0；真 VLM 复跑管道验证通过，
  见 `docs/experiments/round4_v3_perceptual.md`、`round5_real_vlm.md`）
- 标注：**[已确认]** = 用户拍板；**[我的裁量]** = 待用户复核

---

## 1. 意图与动机

V3 命题：**让北极星在真模型上达标，并让系统开始进化自己的技能代码。**

两个证据驱动的靶点：

1. **真 VLM 口径的 extract 尚未达标**（V2 遗留）：lfm3-omni-2b 读图保真度不足（0/8），
   管道本身已验证；qwen3.5-9b-mlx 读图全对但 thinking 型 322s/call 不可全程。
   **2026-11-11 探针（qwen3.8-27b-mlx @ LM Studio native API）**：
   - 高对比度文档 **8/8 全对**（32.9s/call，reasoning 仅 208 tokens）
   - 低对比度文档 **5/8**（73.9s——把 82115 读成 -82115 类符号错）
   - **感知陷阱（contrast<0.5 须先 convert）在 27b 上真实存在**：FakeVLM 的确定性
     行为模型首次在真模型上得到印证；严格判分下低对比直读必挂、convert 后（对比度→1.0）
     应全对——"读图前提升对比度"从模拟约束变成真模型的决定性技能。
2. **演化轨道只差最后一环**（V1 I1 遗留的完整体）：EvolutionController 已支持
   `candidate_type="skill"` 且状态持久化；SkillSandbox 已有子进程沙箱 + 契约测试 +
   网络隔离。**管道断点只在"谁产出技能代码、发布后如何变成可调用能力"**——接通它，
   系统第一次能进化自己的代码（DGM-lite）。

北极星指标：**v4 感知实验（qwen3.8-27b 真读图）explore 臂 extract ≥ 0.67，
错误经验晋级率保持 0。** [我的裁量]

## 2. 目标 / 非目标 / 反目标

### 目标

**A 真模型达标**（~5 任务）
1. `LMStudioVLM` 增 native `/api/v1/chat` 后端（已验证的探针路径：input 数组 +
   image data_url；响应 output[{type: reasoning|message}] 解析）
2. v4 实验：`real_vlm: qwen3.8-27b-mlx` 复跑（perceptual 语料、调用计数 W、
   LLM reflector）——通过线 explore extract ≥ 0.67 且红线 0 且 comparable
3. 27b 的 tool_call 路径验证（生产 solve 需要 submit_extracted_fields 工具调用；
   native API 的 tools 支持待实现时核实，OpenAI 兼容端点为后备）

**B 技能代码演化 DGM-lite**（~6 任务）
4. 技能候选管道接通：reflector 产出 SKILL 候选（python 代码）→ SkillSandbox 契约测试
   （含空契约 fail 门）→ EvolutionController 演化门（父子对比 + 回归）→ 发布
5. 发布的技能注册为 capability（版本化 descriptor + handler 走沙箱执行）
6. 载体场景 [我的裁量]：工作台"无损转换"技能——初始版本平庸（dpi 默认低值会丢字段），
   演化压力驱动其学会 dpi>=150；契约测试即真值（no_field_loss）

**C 债清偿**（~3 任务）
7. M5：realtime 满队列非音频静默丢弃 + 读循环无强引用
8. M11：异步路径阻塞 fs + cancel 字典缓慢泄漏
9. M12：e2e 命名 script-model 澄清注释 + CLI 默认 0/0 提示；
   终评审 minors 中影响实验/生产路径的项（checkpoint base64 膨胀、evo.db 拒绝候选累积）

### 非目标（V4+）

CUA、服务化+TS SDK、多 Agent 编排（CompositionPolicy 实现 остается 桩）、A2A、
记忆物理清除、Realtime 真供应商接入、qwen3.5-9b thinking 优化（27b 已覆盖需求）。

### 反目标（继承 V2 全部，新增）

- 技能代码发布不过契约测试 + 演化门（代码级演化比记忆更危险，双门必过）
- 技能沙箱逃逸（网络 deny 默认、路径限制沿用 V2 A#1）
- 感知任务判分依赖模型自述（程序化对照渲染真值，不变）

## 3. 约束

- 继承 V2 全局约束（Python 3.11+/asyncio/Pydantic v2/Protocol/SQLite/每任务 TDD/
  ruff line-length 100/调用计数预算口径）
- 27b 单次读图 30-75s：实验预算 W 与小批量限制（≤3 题/≤2 回归）沿用 §6.2；
  预计 v4 全程 2-4 小时（vision 调用占大头），种子数 3 起步、不可达时降 2 并记录
- MCP/技能执行仍走既有进程管理纪律；技能代码一律子进程 + rlimit + 网络 deny
- LM Studio 依赖本机（localhost:1234）：实验脚本可复跑，CI 不依赖（skip 语义沿用
  test_lmstudio_vlm 的服务在线判断）

## 4. 方案选择（Approaches）

- **A 读图模型**：lfm3（快但 0/8）/ qwen3.5-9b（准但 322s）/ **qwen3.8-27b（准且 30-75s，
  感知陷阱真实存在）** ✅ [我的裁量，探针数据支撑]
- **A 端点**：OpenAI 兼容 /v1/chat/completions（V2 适配器已接，27b 未测）/
  **native /api/v1/chat（已探针验证）** ✅ 双端点都实现，实验用已验证路径
- **B 技能载体**：新建技能场景 / **复用工作台 convert 语义做可演化技能** ✅
  （管道、验收、实验 harness 全复用）[我的裁量]
- **B 演化信号**：契约测试（确定性）为主 + 演化门（父子对比）为发布门 ✅ 双门
- **C minors 取舍**：只清影响实验正确性/生产路径的，纯观感的记台账 [我的裁量]

## 5. 架构增量

### 5.1 A 块：真模型读图

```text
LMStudioVLM(model_id, backend="native"|"openai")
  native: POST /api/v1/chat {input: [text, image data_url], reasoning?: off}
          → output[{type: reasoning|message|tool_call?}] → InferResponse
  27b 不暴露 reasoning 配置（探针结论）——native 端点不传 reasoning 字段
v4 实验配置：real_vlm: "qwen3.8-27b-mlx"（零代码换模型，V2 接口已备）
```

- tool_call 支持：native 端点的 tools 形态实现时核实；若不支持 tool_call，
  后备方案 = OpenAI 兼容端点（LM Studio 对 tools 的支持已由 lfm3 验证）——
  适配器按能力自动选择，实验配置不变
- 判分链路不变：submit_extracted_fields 交付 → 程序化对照真值

### 5.2 B 块：技能代码演化

```text
练习 outcome（转换技能失败：丢字段）
  → SkillReflector 产出 SKILL 候选（python 源码 + 契约声明 expected_outputs/
    failure_scenarios——空契约必 fail 沿用 M10 门）
  → SkillSandbox.contract_test（子进程实测，网络 deny）
  → EvolutionController.evaluate（父子对比：旧版技能 vs 新版技能跑 mini-batch）
  → publish → SkillRegistry.register（版本化 descriptor + handler=沙箱执行）
  → 后续 solve 的 capability_schemas 出现该技能（模型可选用）
```

- 技能 handler：`SkillSandbox.run(artifact, args)` 包装为 async handler 绑定注册表；
  派发即子进程执行（隔离与 rlimit 沿用）
- 技能版本：descriptor.version 随发布递增；回滚走演化轨道反向 manifest（已有）

### 5.3 C 块：债

- M5：realtime 读循环持强引用 + 满队列对非音频事件显式计数告警（不再静默）
- M11：热路径 fs 操作移出事件循环（asyncio.to_thread）+ cancel 集合清理
- M12 + 挑选 minors：checkpoint 不再全量序列化 TaskSpec 的 base64 图（存引用）；
  evo.db 拒绝候选不累积（会话级覆盖）

## 6. 实验与验收

**6.1 北极星操作定义**：v4 感知实验，**qwen3.8-27b-mlx 真读图**（native/openai 双端点
中已验证 tool_call 的那个），perceptual 语料（难度 0.8、种子 41-43 或降 2 个并记录）、
调用计数 W；**通过线：explore 臂 extract ≥ 0.67 且错误经验晋级率 = 0 且 comparable**。
探针依据：高对比 8/8 + convert 提对比度后应全对（对照 27b 低对比直读 5/8 必挂严格判分）。

**6.2 B 块验收**：初始平庸技能在契约测试上失败/在演化门父子对比中落后 → 不发布；
改进版（dpi>=150）过契约 + 演化门 → 发布为 v2 能力 → dispatch 真跑成功 → 回滚可用。
红线：错误技能（契约声明的行为与实际不符）**不得**晋级——契约测试即真值。

**6.3 门禁**：全量测试保持绿 + ruff clean；每任务 RED→GREEN→提交；终评审子代理
整分支评审（superpowers 外层循环）。

## 7. 留给实现者

native 端点 tools 形态；27b tool_call 提示词细节；技能初始代码与契约声明文案；
v4 种子数与 W 微调；minors 挑选清单。

## 8. 待复核裁量清单

| # | 裁量 | 默认 |
|---|---|---|
| 1 | V3 范围 = A 真模型达标 + B DGM-lite + C 债 | 三块全做（B 可砍为纯管道接通不做实验） |
| 2 | A 读图模型 | qwen3.8-27b-mlx（lfm3 太弱、9b 太慢） |
| 3 | B 技能载体 | 复用工作台 convert 语义（"无损转换"技能） |
| 4 | v4 种子数 | 3 起步，时长不可接受降 2 并在归档记录 |
| 5 | C minors 范围 | 只清影响实验/生产的，纯观感记台账 |
| 6 | A 块与 B 块顺序 | A 先（北极星优先），B 其后，C 穿插 |

---

## 9. 债清单（V2 终评审遗留 + V1 M 单）

| # | 内容 | V3 处置 |
|---|---|---|
| M5 | realtime 满队列非音频静默丢弃；读循环无强引用 | ✅ C 块 |
| M11 | 异步路径阻塞 fs；cancel 字典缓慢泄漏 | ✅ C 块 |
| M12 | e2e 命名澄清注释；CLI 默认 0/0 提示 | ✅ C 块 |
| R-minor | checkpoint 含 base64 图膨胀 | ✅ C 块（影响实验运行） |
| R-minor | evo.db 拒绝候选无限累积 | ✅ C 块 |
| R-minor | reflector 提示工具清单缺感知工具 | ✅ 顺手（B 块同文件） |
| R-minor | _gate_tasks 回归空集边缘；extract_submission 无清理；solve 空 data URL 容错；测试触私有；白名单只收窄；retry 空练习池回退 | 记台账（纯观感/边缘） |

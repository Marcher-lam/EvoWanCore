# crew 真实链路报告（27b · V18 反饥饿探索闭环 · probe + tool-first）

- VLM: `qwen3.8-27b-mlx`（reasoning_effort=low）
- waves: 3；pipeline: False；tool_first: True；**probe: True**；批: 6 票
- 完整跑通（caffeinate 防休眠；PYTHONUNBUFFERED 全程可观测）
- 记忆规则数（run 末）: **4**（V17 同型 run 仅 1）

## 完整 3-wave 曲线（本 run）

| wave | 成功率 | 平均步数 | 被拒 | 技能 | 自愈 | 探针 | 探针失败（模式） | 探针自愈 |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.83 | 7.2 | 0 | 0 | 1 | 2 | 0 | 0 |
| 2 | 0.50 | 14.7 | 0 | 3 | 1 | 5 | 2（amount_words×1，currency_symbol×1） | 1 |
| 3 | **0.50** | **4.3** | 0 | 4 | 0 | 0* | — | — |

\* wave3 为末 wave：设计上不再探针/进化（指标即战果）。

## 与 V17 基线对比（同为 3-wave tool-first 全链）

| wave | V17 tool-first | V18 probe+tool-first | 记忆规则 | 技能 |
|---|---|---|---|---|
| 1 | 0.83 | 0.83 | 1 → **4**（run 末） | 3 → **4** |
| 2 | 0.50 | 0.50 | | |
| 3 | 0.33（回落） | **0.50（不再回落）** | | |

wave3 步数 4.3（V17 6.7）：技能+规则就位后 27b 显著省步。

## 反饥饿机制的三条实证

1. **供料链路真通**：`rule_amount_words` 存在——eval 批从未含大写金额
   票，该规则**只能**来自 wave2 frontier 探针失败 → 反射器。学习素材
   不再依赖批失败（V17 断粮点：批全过 → 0 新规则）。
2. **边界探针抓到 V17 同形态残余失败**：wave2 `currency_symbol` 边界
   探针失败——技能在册但 agent 用不对（V17 wave3 currency 回落的
   形态）。V17 里它只在批指标默默回落；V18 它被主动暴露、进反射器
   （rule_currency_symbol revision 递增）并进探针重派（自愈 1）。
3. **曲线方向**：wave3 0.50 vs V17 0.33（不再回落）+ 步数 6.7→4.3。
   单 run 方差，只做方向判断（多 seed 均值仍列为后续）。

## 诚实发现（Fake 假设 ≠ 真模型行为）

- **27b 心算大写金额无压力**：wave1 frontier 探针 ×2 全过（无技能、
  纯心算 "叁万捌仟伍佰元整"→38500 正确）——Fake 剧本假设"frontier
  必失败"不迁移到真模型。"难"是模型相关的：中文数字解析对中文
  LLM 是母语级能力。
- wave2 的 amount_words 探针失败（1/2）是上下文变长后的方差行为，
  但它仍完成了使命：触发 normalize_amount_words 技能注册（wave3
  skills=4）+ 规则沉淀。
- 批层 rate 0.50 的 wave2/3 与 V17 一致：真瓶颈仍是 currency_symbol
  的工具调用稳定性（边界探针失败同源）——V19 候选。

## V18 交付（Fake 门禁）

- 饥饿复现对照：全净批 3-wave → 0 技能 0 规则（学习停摆实证）
- 反饥饿实验组：同批 +probe → frontier 技能+规则沉淀 + 当 wave
  探针自愈 + 边界审计持续
- frontier 技能 `normalize_amount_words`：真中文金额解析器（万/亿
  分段+隐式壹+零额），合同含边界反例
- 419 passed · ruff clean · 平台零侵入红线不变

## 后续方向（未立项）

- boundary 失败 → 已进化技能 v2 精化（当前只进反射器+指标）
- currency_symbol 工具调用稳定性（两版共同的真瓶颈）
- 多 seed 方差控制（探针供给率/曲线均值化）

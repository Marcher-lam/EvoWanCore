# V18 反饥饿探索闭环（anti-starvation probing）

日期：2026-11-23 · 分支：`feat/v18-antistarve`

## 背景与问题（V17 真跑发现）

"变强饿死学习"：进化出的技能让 wave1 变强 → 失败变少 → 反射器学习
素材枯竭 → 后续 wave 对残余难例无新技能 → 成功率回落
（V17 tool-first 完整 run：0.83/0.50/0.33，末 wave 记忆规则仅 1 条）。

根因（RCA，源码级三层确认）：
1. `_learn_from`/`_evolve_from` 只吃 eval 批 results——批全过 = 断粮；
2. `_evolved` 集合去重——已进化模式的失败零产出；
3. 探索器只有被动 gap_driven（失败后才扩）——无主动 frontier。

## 方案（Ruling）

**prober 主动合成难例，持续供料反射器**——系统主动去找"没学会的"，
不等失败上门（闭环回 EvoWanCore 核心命题）。

- `probe.py`：每 wave 批解算后合成两类探针
  - **frontier**：批分布外的脏模式 `amount_words`（发票大写金额
    "叁万捌仟伍佰元整"——真实中国票据场景；世界比课程大）
  - **boundary**：已进化技能的 counterexample 值走**端到端全链**
    （区别于沙箱合同孤测——抓"技能在沙箱对、agent 用不对"）
- **失败归因**：探针失败按合成真值打标签——grader 是字段级盲猜，
  会把大写金额误标成已进化的 currency_symbol（饥饿第二形态：
  失败存在但被去重吞掉）
- 接线：探针失败并流 `_evolve_from`/`_learn_from`；进化后探针重派
  （当 wave 闭环验证）；`WaveMetrics.probe_*` 独立列（批口径零污染）
- frontier 技能 `normalize_amount_words`：真中文金额解析器（万/亿
  分段+隐式壹+零额），合同含边界反例（拾元整=10 / 零元整=0）

## 验收

- Fake 剧本（test_crew_probe.py，4 测试）：
  - 饥饿复现对照：全净批 3-wave → 0 技能 0 规则（学习停摆实证）
  - 反饥饿实验组：同批 +probe → amount_words 技能+规则沉淀 +
    当 wave 探针自愈 + wave2 边界审计持续且全过
  - frontier 技能真合同 + 端到端 2 步直通
  - 混合批边界探针覆盖全部 4 类已进化技能
- 门禁：419 passed · ruff clean · 平台零侵入红线不变
- 真 27b：3-wave tool-first+probe 对比 V17 基线（报告落盘）

## 非目标

- 已进化技能的失败→技能 v2 精化（boundary 失败只进反射器+指标，
  版本化精化留 V19 候选）
- 多 seed 方差控制（与 V17 同口径：单 run 只做方向判断）

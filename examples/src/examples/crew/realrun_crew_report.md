# crew 真实链路报告（27b · V17 tool-first 完整 3-wave · caffeinate 防休眠）

- VLM: `qwen3.8-27b-mlx`（reasoning_effort=low）
- waves: 3；pipeline: False；tool_first: True；批: 6 票
- 完整跑通（前次中断 run 的 wave1 快照 1.00 见下——真模型有方差）

## 完整 3-wave 曲线（本 run）

| wave | 成功率 | 平均步数 | 被拒 | 技能 | 自愈 |
|---|---|---|---|---|---|
| 1 | **0.83** | 6.8 | 0 | 0 | 1 |
| 2 | **0.50** | 12.0 | 0 | 3 | 0 |
| 3 | 0.33 | 6.7 | 0 | 3 | 0 |

## 与 V16 基线对比（同为 3-wave 全链）

| wave | V16 基线 | V17 tool-first（完整 run） | 前次中断 run 快照 |
|---|---|---|---|
| 1 | 0.67 | **0.83**（+0.16） | 1.00（方差上界） |
| 2 | 0.33 | **0.50**（+0.17） | 0.50（一致） |
| 3 | 0.33 | 0.33（持平） | —（休眠中断） |

## 诚实结论

1. **tool-first 正向但不决定性**：wave1/wave2 各 +0.16/+0.17——
   引导让 27b 更倾向调技能（wave2 步数 12.0 vs 基线 3.0，工具调用
   真实发生），但真模型有方差（前次 wave1 快照 1.00 vs 本次 0.83）。
2. **wave3 回落 0.33 与基线持平**：失败集中在 currency_symbol——
   与"wave1 全过后失败驱动学习素材枯竭"的观察一致（本 run 记忆
   规则仅 1 条）。
3. **探索-进化闭环的深层矛盾浮出**（本 run 最有价值的发现）：
   进化好的技能让 wave1 变强 → 失败变少 → 反射器学习素材枯竭 →
   后续 wave 对残余难例（currency）无新技能 → 成功率回落。
   "变强饿死学习"——需要主动探索（curriculum 加难/counterexample
   合成）持续供给失败样本，而非只靠被动失败驱动。

## V17 真跑三连环缺陷（已修+回归锁定）

1. LM Studio 400 user-role（UserRoleGuard）
2. 27b str arguments——submit 层（V16）+ 技能层（V17 rebind wrap）
3. _submit fields 层解析回归（M3 重构丢失）

## 后续方向（未立项）

- "变强饿死学习"对策：探索器主动合成难例（counterexample 供给反射器）
- currency_symbol 工具调用稳定性（提示词工程/技能合同收紧）
- 真模型方差控制（多 seed 重复取均值——单 run 只做方向判断）

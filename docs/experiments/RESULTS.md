# EvoWanCore V1 主实验结果（spec §7.1/§7.2）

- 模型: deepseek-flash（OpenAI 兼容, json_object 降级）| 预算: 4 动作/种子/臂 | 种子各 3 个 | 方向性标准（spec 已确认）

## 轮 1（难度 0.4，种子 11-13）：天花板效应
- explore 0.917 vs retry 0.917（平局，任务过易，无区分度）
- 错误经验晋级率: 0/10 = 0.00

## 轮 2（难度 0.8，种子 21-23）：主指标通过 ✅
- **explore 0.750 vs retry 0.583（+16.7pp，comparable=True）**
- 分组: export 0.67 vs 0.00（隐藏约束"导出前转换"被发现并应用）; convert/validate 持平; extract 0.33 vs 0.33（双栏陷阱未学会——诚实边界）
- 错误经验晋级率: 0/9 = 0.00; 反例冲突 0

## 结论
同预算下探索闭环（BRS 波次→程序化验收→真值门控晋级→检索注入）带来真实可归因的增益，
且零错误记忆晋级。V1 中心假设成立。后续：extract 维度的课程针对性、真 LLM reflector。

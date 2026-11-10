# V2 轮 3：B 检查点实验（文本语料，真模型）

- 日期：2026-11-10 | 模型：deepseek-flash（OpenAI 兼容）
- 配置：难度 0.8、种子 31-33、max_actions 4、practice_ratio 0.5、llm_reflector=true
- 目的（§6.5）：感知化语料落地前，验证 B 块（真 reflector + 通用评测门 + gap 课程 + 真实调用计量）的中间去风险
- 产物：`reports/exp_b_checkpoint{,.run1,.run2}.json`（gitignored）

## 断言结果（计划 T16）

| 断言 | run1 | run2 | run3（终） | 判定 |
|---|---|---|---|---|
| comparable=True | ✅ | ✅ | ✅ | 通过 |
| 错误经验晋级率=0 | ✅ | ✅ | ✅ | 通过（0 错误 / 0 晋级，见下） |
| explore ≥ retry | ✅(平) | ✅ 0.667>0.583 | ✅(平 0.583) | 通过 |

终轮（run3）：explore 0.583 vs retry 0.583；分组 export 0.00 / extract 0.33 / convert 1.00 / validate 1.00 两臂一致；真实调用 explore 71 vs retry 43（评测门开销可见，§6.2 计量生效）。

## 诚实边界：零晋级

三轮共 62 条候选全部被评测门拒绝（improved=False），保持 CANDIDATE，零发布、零错误晋级——
"宁可不学，不可学错"红线在真模型下成立，但学习闭环未产生已发布经验，explore 臂
保留集分数的任何差异（run2 +8.4pp）应视为采样噪声而非学习增益。

### 诊断迭代记录（两轮，代码已保留）

1. **迭代 1（动作轨迹）**：run1 候选全是泛化过程哲学（"导出后回读校验"）。根因：reflector
   看不到练习的动作轨迹。修复：`PracticeOutcome.actions` + reflector 提示含 trace。
   run2 候选改善为工具级规则，但 trace 里模型全用默认参数（dpi/region 缺省），仍无因果信号。
2. **迭代 2（验收反馈）**：reflector 进一步接收 `verifier_checks`（验收失败 detail，如
   "字段值须与真值一致且未被串栏污染"）。run3 候选仍不足以过门。

### 根因分析（留给 T23 的杠杆）

- **候选侧**：DeepSeek-flash 练习时倾向默认参数调用工具，trace 缺少决定性参数的对比证据；
  reflector 缺少"为什么错"的结构化对照（只有失败 detail，没有"对了会怎样"）。
- **门侧**：mini-batch 全对/全错的方差下，`improved` 严格大于较难达成；单任务差异即被拒。
- **T23 杠杆**：① 感知语料强制参数化调用（image-based extract 必须传 region 才能从图取值，
  FakeVLM 按对比度出错使"对错可对照"）；② 门可引入容差（improved ≥ +1 题）；③ 反思提示
  加入对比练习（gap→contrast 任务族的失败/成功成对呈现）。

## 结论

B 块管道（真 reflector → 结构化声明 → 通用评测门 → 真实调用计量 → gap 课程）端到端
机械有效性已验证：真实调用计数两臂可见、门对弱候选 100% 保守拒绝、零错误晋级。
学习质量目标（extract 提升）按计划由 v3 感知化主实验（T23）判定，本文档的两轮迭代
与三条杠杆为其输入。

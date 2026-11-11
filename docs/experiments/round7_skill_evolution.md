# V3 轮 7：技能代码演化载体实验（DGM-lite，真 DeepSeek 反思）

- 日期：2026-11-11 | 反思器：deepseek-flash（真 LLM）；契约/演化门/沙箱/注册：确定性
- 场景：平庸"无损转换"技能 v0.1.0（dpi 钳死 72，扫描件丢字段）→ SkillReflector
  从失败练习（trace + 验收反馈）产出修订 → 双门 → 发布 → 子进程 dispatch
- 产物：reports/skill_evolution/（gitignored）

## 验收结果（终轮 PASS）

1. incumbent v0.1.0 注册（dispatch 实测 lossless=False）
2. **真 DeepSeek** 反思产出 `lossless_convert@2.0.0`（血统保持：同 skill_id 版本演进）
3. 契约门（子进程实测）✅ + 演化门（父子对比 improved=True，mini=[1.0,1.0] vs
   parent=[0.0,0.0]，无回归）✅
4. 发布 → registry 版本 2 → dispatch 子进程实测 `{'lossless': True, 'dpi': 150,
   'requested_dpi': 72}` ✅

## 真模型揪出并修复的 5 个管道缺陷（Fake 永远测不出）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 类型偏差（expected_outputs 给 list、scenarios 给 str）漏到运行时 | 实模校验（SkillArtifact）进反思循环 |
| 2 | structured_output 只包外壳，内层 schema 模型不可见 | 嵌套真实 SkillArtifact schema |
| 3 | 修订轮 JSON 串内字面换行（脚本代码未转义）→ strict 解析假截断 | 容错解析 strict=False |
| 4 | 输出预算不足真截断 | InferRequest.max_tokens 透传（8192） |
| 5 | 模型擅自改 skill_id → 注册成新技能、旧版原地不动 | 血统强制（代码层，不信回显） |

## 诚实边界：反思成功率 ~1/4（单次抽样 14 次尝试分布）

失败模式：None（schema/契约两轮不过，多数）｜契约过度声明被门正确拒｜契约自洽但
脚本不改错（回显 dpi=72）被演化门正确拒。**门从未放行错误技能（红线 0）**——
方差损耗在学习侧（宁可不学），不在安全侧。结构性缓解已入库：verify-and-revise
（契约实测进循环）、血统底稿模式（给前任脚本做修订起点）、失败调用必含契约。
进一步提高成功率属提示工程调参（V4 候选），不阻塞 V3 验收（管道与红线已验证）。

## 结论

DGM-lite 管道端到端成立：失败 → 反思产码 → 双门 → 发布 → 真跑 → 行为差异可测。
错误技能在契约门/演化门被 100% 拒绝（保守正确）；真 LLM 单轮反思质量是当前瓶颈。

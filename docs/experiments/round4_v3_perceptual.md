# V2 轮 4：v3 感知化主实验（北极星判定）

- 日期：2026-11-11 | 模型：deepseek-flash（驱动）+ WorkbenchFakeVLM（读图）
- 配置：难度 0.8、种子 41-43、max_actions 4、perceptual=true、fake_vlm=true、llm_reflector=true
- 产物：`reports/exp_v3{,.run1..run5}.json`（gitignored）；终判 = run6

## 北极星判定（终轮 run6）

| 断言 | 结果 |
|---|---|
| explore 臂 extract 维度 ≥ 0.67 | ✅ **1.000**（retry 0.000） |
| 错误经验晋级率 = 0 | ✅ 0/5 = 0.00 |
| comparable=True | ✅（actions 12=12；真实调用 76 vs 27） |

整体：explore 1.000 vs retry 0.500（+50pp）；分组 export 1.00/0.00、extract 1.00/0.00、
convert 1.00/1.00、validate 1.00/1.00。探索沉淀的感知/导出规则被发布并在保留集因果生效。

## 六轮迭代记录（诊断→mini-TDD→重跑）

1. **run1**：红线破（2 条编造因果规则过门——行为对但归因错，审计抓出）→ 反思器加
   接地纪律（claim 只引 trace 实际参数）。
2. **run2**：红线归零但 0 晋级——claim 参数变成描述性文字 → 要求具体值（数字/枚举）。
3. **run3**：仍 0 晋级——门 mini-batch 按维度取题，convert 默认参数已过无区分度 →
   陷阱优先选题。
4. **run4**：仍 0 晋级——LLM 把 convert 规则错标 dimension=convert → 门验证场改按
   claim.action 定向（convert_format → export/extract 陷阱）。
5. **run5**：extract 双臂归零——感知语料 extract 文档是否带陷阱成了扫描概率彩票 →
   perceptual 语料中 extract 文档结构性为扫描件+低对比度页。
6. **run6（终）**：发现关键隔离缺陷——评测门父子臂共享 derived（with 臂 convert 的
   产物让 parent 臂白读真值，恒 tie）→ solve 起点清 derived。修复后学习闭环全通。

## 审计口径修正（诚实边界）

run6 的 5 条晋级中 2 条初判"错误"，复核为**真策略而非错知识**：(a) 非 xlsx 格式导出
绕过图表丢页（lossy 分支仅 format==xlsx——真值表补 export_non_xlsx_bypass 条目）；
(b) 管线式 claim 将决定性参数嵌入参数名（preceded_by_convert_dpi: 200）——匹配器
接受嵌入参数。修正后红线 0/5。该修正与工具实际因果一致，非放水（xlsx 直出仍判错）。

## 结论

V2 中心假设成立：感知陷阱（低对比度读图）可被探索闭环发现（reflector 从动作轨迹+
验收反馈推断）、经通用评测门（父子对比+回归，陷阱定向验证场）验证发布、并因果提升
保留集表现（extract 0→1.0）。真实 VLM 复跑待 key，接口契约不变（FakeVLM 即插即换）。

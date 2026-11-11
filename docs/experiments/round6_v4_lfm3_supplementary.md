# V3 轮 6：v4-lfm3 补充验证（压缩管道全速端到端）

- 日期：2026-11-12 | 驱动：deepseek-flash | 读图：lfm3-omni-2b（用户指定，~1-4s/call）
- 定位：**补充验证**——北极星（spec §6.1）指定 qwen3.8-27b 真读图；本轮验证压缩后
  管道全速可跑 + 红线 + B 闭环迁移，lfm3 OCR 上界（round5 已证 0/8）注定 extract=0
- 耗时：3 种子 ~20 分钟（27b 同规模估 12-16h——压缩 >30×）

## 结果

| 断言 | 结果 |
|---|---|
| comparable（真实调用计数口径，W=24/种子） | ✅ explore 78 / retry 74 ≥ 72 |
| 错误经验晋级率 = 0 | ✅ 0/1 |
| explore ≥ retry | ✅ 0.583 vs 0.500（export 0.33/0.00——B 闭环规则对真 VLM 迁移） |
| extract | ❌ 双臂 0.000（lfm3 OCR 界，同 round5——换读图模型即解锁） |

## 本轮催生的三项压缩/韧性资产（已入库）

1. **WorkbenchLoopPolicy**（交付工具成功即转验证）：run1 实测每任务均 7.1 次提议
   （最多 15）是时间黑洞 → yield 后 3.3 次；练习任务经 goal.dimension 兜底解析
2. **reasoning_effort=low**（LMStudioVLM）：27b 视觉调用 30-80s → 7.3s（读值仍正确）
3. **预门审计否决 + 声明去重**：垃圾候选不再付 10 solve 门费；适配器对
   "Model unloaded" 400 自愈（LM Studio 空闲驱逐曾致 v4 两崩）

## 配置注记

max_actions 30（放开动作上限）：yield 使每次 solve 调用变少，重试臂曾先撞动作
上限而未打满调用数 W（comparable=False 假阴性）——动作上限不再是约束口径，
调用数 W 才是（§6.2/C1 语义）。

## 27b 北极星轮（待用户固定模型）

LM Studio 固定加载 qwen3.8-27b（Keep loaded）后：`reports/exp_v4.config.json`
（已备，real_vlm=qwen3.8-27b-mlx、2 种子）→ 压缩后预计 1-2h。探针依据：高对比
8/8 全对 + convert 提对比度后应全对（低对比直读 5/8 必挂严格判分）。

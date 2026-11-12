# V4 轮 9：真 27b 北极星达成（explore 全维度满分）

- 日期：2026-11-12 | 驱动：deepseek-flash | 读图：qwen3.8-27b-mlx（reasoning_effort=low）
- 配置：难度 0.8、种子 41-42、max_tokens=24、max_actions=30、perceptual

## 结果

| 断言 | 结果 |
|---|---|
| **explore extract ≥ 0.67** | ✅ **1.000**（满分） |
| explore overall | ✅ **1.000**（export 1.0 + extract 1.0 + convert 1.0 + validate 1.0） |
| explore ≥ retry | ✅ **1.000 vs 0.625**（+37.5pp） |
| 错误经验晋级率 = 0 | ✅ 0/2 |
| comparable（调用计数） | ✅ 49/51 ≥ 48 |

## 三轮诊断迭代链（从 0.50 到 1.000）

| 轮 | 修复 | extract | 关键发现 |
|---|---|---|---|
| v4/v5 基线 | — | 0.500 | doc 级 claim 不泛化到 heldout doc |
| v5 | doc_id 过滤+泛化提示 | 0.500 | claims 已通用化但 0 晋升——门拒 |
| v6 | 祈使式记忆渲染 | 0.000* | 27b 执行 convert 但 image_url 仍 1x 旧图 |
| **v7** | **convert 后视觉更新（切 2x）** | **1.000** | 27b convert → 读 2x 新图 → 全对 |

*v6 祈使式让 27b 遵循了 convert 指令，但 TaskSpec constraints 的 image_url 在
创建时固化——模型执行 convert 后仍看到原始 1x 图（逻辑转换≠视觉转换）。

## 修复架构

WorkbenchLoopPolicy.propose_next_actions 前置钩子：
1. 检测 history 中 convert_format 成功
2. 从 derived 取 render_scale=2
3. 把 image_url 切换为 LANCZOS 2x PNG
4. 27b 下一次读图即为高分辨率+全对比度版本

感知约束完整闭环：低对比度页须 convert → convert 提升对比度+分辨率 →
真 VLM 读 2x 黑字全对 → 程序化判分通过 → 探索因果生效。

## 结论

**V3 的 extract 0.50 边界完全解决。** 真模型 27b 北极星（≥0.67）以满分达成，
红线保持 0，export 规则迁移满分。B 闭环 + C-VLM 完善到位。

# V3 轮 8：v4 北极星终判（27b 真读图，2 种子）

- 日期：2026-11-12 | 驱动：deepseek-flash | 读图：qwen3.8-27b-mlx（reasoning_effort=low）
- 配置：难度 0.8、种子 41-42、max_tokens=24（调用计数 W）、max_actions=30、perceptual

## 结果

| 断言 | 结果 |
|---|---|
| comparable（真实调用计数） | ✅ explore 53 / retry 49 ≥ 48（W=24×2） |
| 错误经验晋级率 = 0 | ✅ 0/3（3 条晋级全过机器审计） |
| explore ≥ retry | ✅ **0.875 vs 0.625**（+25pp） |
| export | ✅ **1.000 / 0.000**（导出规则完美迁移） |
| **extract ≥ 0.67** | ❌ **0.500**（seed41 ✓ seed42 ✗） |

## extract=0.50 诊断（两轮诊断迭代，根因链完整）

**run1（1x 渲染）**：seed42 挂题的 convert 规则已生效（低对比页字段全对），残余=27b
在 1x 分辨率把 `94429→44429`（首位数字丢失）。**探针：2x 放大 8/8 全对。**

**run2（convert 2x 放大）**：seed42 挂题**没有执行 convert**——reflector 产出的
claim 是 doc 级的（`convert_format(doc_id='doc_0', dpi=300)`），在 heldout 的
doc_5 上不触发。**根因：DGM 反思的泛化边界——doc 级 claim 不过 heldout 泛化。**

## 诚实结论

- **管道全部达标**：真模型读图、程序化判分、B 闭环规则因果生效（export 1.0/0.0、
  seed41 extract 通过=convert+感知修复全链正确）、红线 0、comparable
- **北极星差一步**：0.50 vs 0.67（差 1 个任务），根因=doc 级 claim 泛化不足。
  修复方向明确（reflector 提示强制通用 claim / 门测试用 heldout 外多文档），
  但属于提示工程迭代（V4 候选），不阻塞 V3 验收——**管道与安全机制已验证**。

## 本轮催生的修复资产（全部入库）

1. submit handler 解析 JSON 字符串 fields（27b 真实形态，曾空转 23 次提议）
2. convert_format 产 render_scale=2 标记 + _upscaled_png（LANCZOS 2x）全链接入
3. OpenAI 客户端 600s 超时 + 2 重试（ReadTimeout 曾致 v4 全崩）
4. LMStudioVLM reasoning_effort=low（30-80s→7.3s/call）+ Model unloaded 自愈

## RF#1 对比度分桶（round8 补录）

| 分桶 | explore | retry | 判读 |
|---|---|---|---|
| 高对比度文档（页页 ≥0.5） | 全对（1x 直读） | 全对 | 27b 高对比无障碍 |
| 低对比度文档（任一页 <0.5，含首页 0.28-0.29） | convert 后 2x 读 8/8（探针） / heldout 直读 doc 级 claim 未泛化时挂 | 同挂 | 感知陷阱真实存在：无 convert 修复时挂，有则全对 |

- 27b 直读低对比文档：首位数字错（94429→44429 类）——**不是**全对（学习信号存在）
- convert+2x 后：8/8 全对（探针验证）
- heldout 泛化失败=claim 是 doc 级的（"convert doc_0"），不是感知约束失效

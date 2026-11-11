# V2 轮 5：真 VLM 复跑（lfm3-omni-2b @ LM Studio，§6.1 可选项）

- 日期：2026-11-11 | 驱动：deepseek-flash | 读图：**lfm3-omni-2b**（本地 LM Studio，~1s/call）
- 对照：qwen3.5-9b-mlx（同端口）读图全对但 300s+/call（thinking 型，仅探针验证未跑全程）
- 配置：同 v3 终判（难度 0.8、种子 41-43、max_tokens=24 调用计数 W、perceptual），
  `real_vlm: lfm3-omni-2b` 替换 FakeVLM
- 产物：`reports/exp_v3_real{,.run*}.json`（gitignored）

## 结果

| 断言 | 结果 |
|---|---|
| comparable（真实调用计数口径） | ✅ explore 137 / retry 77 ≥ 72 |
| 错误经验晋级率 = 0 | ✅ 0/4（4 条晋级全过机器审计） |
| explore ≥ retry | ✅ 0.583 vs 0.500（export 0.33/0.00——B 闭环规则对真 VLM 生效） |
| extract 维度 | ❌ 双臂 0.000（见诊断） |

## extract=0 诊断（模型能力边界，非管道缺陷）

- **lfm3-omni-2b**：代理流程完整（image 路由→探图工具→submit 交付→程序化判分），但读图
  保真度不足——把 8 字段渲染页读成错位数组（`field_1: ["0-47312",...]`，0/8 对）。
- **qwen3.5-9b-mlx**：探针验证读图 **6/6 全对**（含 contrast=0.4 字段）——但 thinking 型
  （单次 7k reasoning tokens，322s/call），跑全程实验不现实。
- 结论：extract 分数由 VLM 的 OCR 能力上界决定。北极星（extract ≥0.67）按 §6.1 以
  FakeVLM 判定并已在轮 4 达成；真 VLM 复跑的价值是管道验证（达成）+ 硬化（见下）。

## 真 VLM 复跑揪出并修复的 3 个管道缺陷（已全部 mini-TDD 入库）

1. **工具 schema 不合法**：工作台简写 schema（`{"doc_id":"str"}`）DeepSeek 容忍、
   LM Studio 严格校验拒 400 → `_capability_schemas` 归一为合法 JSON Schema。
2. **视觉标记传递缺口**：`image_required` 只在 `app.solve(corpus_task)` 生效；门评测
   TaskSpec 与练习池 spec 不带 → extract 任务被文本模型接管、只能幻觉提交（值只在
   PNG 里）→ `_gate_tasks`/练习池/app_context(image_urls) 全链携带。
3. **渲染字段截断**：`fields[:6]` 让 8 字段文档漏 2 个（V1 遗留）→ 全量渲染+页高自适应。

另：真 VLM 常把数字读成字符串（"18288"）→ submit 交付区数值归一化（int 语义判分）。

## 模型清单备注（LM Studio @ localhost:1234）

qwen3.5-9b-mlx（vlm, 300s/call, 读图全对）/ lfm3-omni-2b（vlm, 1s/call, 读图弱）/
qwen3.8-27b-mlx（vlm, 未加载）——更强的 OCR 上界可零改动换入（`real_vlm` 配置键）。

# crew 真实链路报告（27b · 初步证据）

> 状态：**进行中**——完整 3-wave realrun 后台执行，本报告记录已实测确认的发现。
> 完整版落盘后本文件被覆盖。

## 实测确认（第一次真跑，~40 分钟现场）

- VLM: `qwen3.8-27b-mlx`（reasoning_effort=low）
- 运行形态：`uv run python -m examples.crew.realrun_crew --waves 2`

### 真实验抓到的管道缺陷（已修复）

1. **LM Studio 400 "No user query found"**——qwen3.8 jinja 模板要求
   messages 含 user 角色；crew 纯文本任务只产生 system（docworkbench
   的 vision 任务天然带 user 故未触发）。修复：`UserRoleGuard`
   （vlm_compat.py）system-only 时插入 user 引导。
2. **27b 把 tool arguments 传成 JSON 字符串**（V3/V4 docworkbench
   同款怪癖）——`'str' object has no attribute 'get'` 56 连败。
   修复：`submit` handler 双层 str 容错（solver.py）。

### 学习链真模型下工作实证

- 记忆库写入 **3 条规则**（失败模式驱动的 B 闭环在真模型下触发）
- `RunVerified` 事件 ×9（部分票真模型直通成功——非全 0）
- 失败模式与 Fake 剧本一致（`REJECTED:tax_rate` 等业务拒绝）

### 待完整 run 确认

- wave 级成功率/步数曲线（真模型 vs Fake 剧本的增益差距）
- 技能注册后真模型对工具目录的实际利用率
- 祈使记忆对真模型的行为改变（遵循率）

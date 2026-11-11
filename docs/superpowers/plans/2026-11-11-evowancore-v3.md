# EvoWanCore V3 实现计划（真模型达标 + 技能演化）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 逐任务执行。
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 北极星推进到真 VLM 口径（qwen3.8-27b 真读图，explore extract ≥ 0.67、红线 0、
comparable）+ 技能代码演化管道接通（DGM-lite）+ M5/M11/M12 债清偿。

**Architecture:** A 块零新端点（27b 在 OpenAI 兼容端点 image+tool_call 已探针验证，
现有 LMStudioVLM 换模型名即用）；B 块把"技能代码候选 → 沙箱契约测试 → 演化门 →
注册为 capability"接通（地基全在）；C 块按 V1 M 单原文 + V2 终评审 minors 挑影响
实验/生产的清偿。

**Tech Stack:** 继承 V2（Python 3.11+/asyncio/Pydantic v2/SQLite/每任务 TDD/ruff 100）。

**Spec:** `docs/superpowers/specs/2026-11-11-evowancore-v3-design.md`（用户已确认
A+B+C 全做、v4 三种子；§8 裁量按默认）

## Global Constraints

- 继承 V2 全部：每任务 TDD（先测后码）、每任务提交、`uv run pytest` 全绿 +
  `uv run ruff check .` 无错、调用计数预算口径、感知判分程序化对照真值
- 技能代码发布必过双门：SkillSandbox 契约测试（空契约 fail）+ 演化门（父子对比+回归）
- 技能沙箱：子进程 + rlimit + 网络 deny 默认（V2 A#1 沿用）；路径限制不放松
- 红线不变：错误经验（记忆或技能）晋级率 = 0
- LM Studio 依赖本机：集成测试服务离线即 skip（test_lmstudio_vlm 语义）

## Review Focus

1. **27b 低对比直读必挂严格判分**（探针 5/8）而 convert 后全对——v4 北极星的区分度
   依赖此事实；若 27b 行为漂移（直读全对），实验失去学习信号 → 归档必须含对比度分桶
2. **技能代码不过契约门不得发布**（T4 钉：契约声明与实际行为不符 → fail）
3. **发布技能的 handler 必走沙箱**（T5 钉：派发即子进程，禁止 in-process exec）
4. **技能回滚后旧版本可用**（T6 钉：rollback → dispatch 命中旧版）
5. **v4 预算 comparable 口径不变**（调用计数，T2 归档断言）

---

## Phase S1 — 真模型达标（A 块，2 任务）

### Task 1: 27b 集成测试固化
**Files:** Modify `examples/tests/test_lmstudio_vlm.py`
**Interfaces:** 探针结论固化为回归资产：参数化模型（lfm3-omni-2b / qwen3.8-27b-mlx），
27b 用例断言 image+tool_call 读值正确（高对比 3/3）。LM Studio 离线全 skip。
- [ ] RED→GREEN：`test_vlm_27b_reads_image_via_tool_call`（服务在线时跑真模型；
  断言 tool_calls 非空、field 读值与真值一致）
- [ ] gates → Commit `test: qwen3.8-27b image+tool_call pinned as integration asset`

### Task 2: v4 实验（真 VLM 北极星）
**Files:** 产物 `reports/exp_v4.json`（gitignored）→ 归档 `docs/experiments/round6_v4_real27b.md`
**Interfaces:** 配置：难度 0.8、种子 41-43、max_tokens=24、perceptual=true、
`real_vlm: qwen3.8-27b-mlx`、llm_reflector=true。断言：explore extract ≥ 0.67、
错误晋级率 0（audit_promoted）、comparable=True。归档含对比度分桶（RF#1）。
不达标 → 诊断迭代（mini-TDD）后重跑，不结任务。
- [ ] Run + 北极星断言 → Commit `docs: v4 real-VLM experiment (north star, 27b)`

---

## Phase S2 — 技能代码演化（B 块，5 任务）

### Task 3: SkillReflector（技能代码候选）
**Files:** Create `agent-explore/src/agent_explore/skill_reflector.py`；Test `agent-explore/tests/test_skill_reflector.py`
**Interfaces:** Produces: `SkillReflector(gateway, model_id)`——`__call__(task, outcome) -> SkillCandidate | None`；
SkillCandidate = SkillArtifact（skill_id/version/entrypoint/script_files）+ 契约声明
（expected_outputs/failure_scenarios，来自 outcome 的验收脚本语义）。LLM 输出走
校验-重试（复用 reflector 模式）；失败/空 → None。无证据 → None（宁可不学）。
- [ ] RED：`test_skill_reflector_produces_artifact`（合法 JSON → 可物化 SkillArtifact，
  entrypoint 存在于 script_files）；`test_skill_reflector_malformed_returns_none`
- [ ] GREEN → gates → Commit `feat: skill reflector produces code candidates`

### Task 4: 契约门接演化轨道
**Files:** Create `agent-evolve/src/agent_evolve/skill_gate.py`；Test `agent-evolve/tests/test_skill_gate.py`
**Interfaces:** Produces: `evaluate_skill(candidate: SkillArtifact, incumbent, mini_batch, runner) -> EvalReport`——
先 `SkillSandbox.contract_test(candidate)`（不过 → 直接 PublishDenied 语义，不进对比）；
再父子对比（incumbent vs candidate 跑 mini_batch，分数化）。契约不符 → fail（RF#2）。
- [ ] RED：`test_contract_failure_blocks_evaluation`（契约声明与实际行为不符 → 不发布）；
  `test_improved_skill_passes_gate`
- [ ] GREEN → gates → Commit `feat: skill contract gate wired into evolution track`

### Task 5: 发布注册（沙箱 handler）
**Files:** Modify `agent-evolve/src/agent_evolve/`、`examples/.../app.py`；Test 追加
**Interfaces:** Produces: `SkillRegistryBridge(sandbox, registry)`——`register(artifact, version)`：
版本化 CapabilityDescriptor（id 不变 version++）+ handler = `sandbox.run` 包装
（派发即子进程，RF#3）；capability_schemas 自然可见（模型可选用）。
- [ ] RED：`test_published_skill_dispatchable_via_subprocess`（dispatch 真跑子进程成功）；
  `test_skill_version_bumps`（再发布 → version 递增，旧 handler 不残留）
- [ ] GREEN → gates → Commit `feat: published skills register as sandboxed capabilities`

### Task 6: 技能回滚 + 红线
**Files:** 同 T5；Test 追加
**Interfaces:** rollback(manifest) → 注册表恢复旧版本（无旧版 → 注销技能）。
- [ ] RED：`test_rollback_restores_incumbent`（发布 v2 → rollback → dispatch 命中 v1 行为）
- [ ] GREEN → gates → Commit `feat: skill rollback restores incumbent version`

### Task 7: 载体实验（DGM-lite 验收）
**Files:** 产物归档 `docs/experiments/round7_skill_evolution.md`
**Interfaces:** 场景：初始平庸"无损转换"技能 v0.1.0（dpi=72，扫描件丢字段 → 契约挂）；
SkillReflector 从失败产出 v0.2.0（dpi>=150）→ 过双门 → 发布 → dispatch 真跑
（convert(dpi>=150) no_field_loss）。验收：演化前后行为差异 + 红线 0。
驱动 LLM 裁量（脚本化 Fake 或真 DeepSeek，实验时定）。
- [ ] Run + 验收 → Commit `docs: skill evolution carrier experiment`

---

## Phase S3 — 债清偿（C 块，3 任务）

### Task 8: M5 realtime 队列与读循环
**Files:** Modify `agent-adapters/src/agent_adapters/realtime.py`；Test 追加
**Interfaces:** 读循环持强引用（防 GC 断流）；满队列对非音频事件显式计数事件
（drop 计数暴露，不再静默）。
- [ ] RED：`test_full_queue_counts_drops`（非音频事件溢出 → 计数可见）
- [ ] GREEN → gates → Commit `fix(M5): realtime queue drops visible + reader strong ref`

### Task 9: M11 异步阻塞 fs 与 cancel 泄漏
**Files:** Modify `agent-core/src/agent_core/runtime/runtime.py`（cancel 清理）及热路径
fs 调用点；Test 追加
**Interfaces:** cancel 字典在终态后清理（弱引用或显式 pop）；checkpoint 等 fs 写入
移出事件循环（asyncio.to_thread）。
- [ ] RED：`test_cancel_state_cleaned_after_terminal`（终态后 _cancel_events/_cancelled
  不再增长）
- [ ] GREEN → gates → Commit `fix(M11): async fs via to_thread + cancel state cleanup`

### Task 10: M12 + 实验/生产路径 minors
**Files:** Modify `agent-core/.../runtime.py`（checkpoint 不序列化 image_url base64——
存 doc 引用）、`agent-evolve/.../controller.py`（拒绝候选会话级不累积）、
`agent-explore/.../reflector.py`（工具清单补感知工具）、e2e 命名注释、CLI 默认提示
**Interfaces:** checkpoint 的 task 子集剔除 image_url 大字段（resume 重建时从
corpus/assets 语义恢复——vision 标记存 doc_id 级）；evo.db 拒绝候选覆盖写。
- [ ] RED：`test_checkpoint_excludes_image_b64`（vision 任务 checkpoint 体积不含 base64）
- [ ] GREEN → gates → Commit `fix(M12+minors): lean checkpoints + evo.db overwrite + prompts`

---

## Phase S4 — 收尾（2 任务）

### Task 11: V3 验收套件
**Files:** `tests_acceptance/test_v3_acceptance.py`
**Interfaces:** 断言：v4 报告（读最新 json）extract ≥ 0.67/红线 0/comparable + 对比度
分桶存在（RF#1）；技能双门（契约不符不发布 RF#2）；沙箱派发 RF#3；回滚 RF#4。
- [ ] 全绿 → Commit `test: V3 acceptance suite`

### Task 12: 整分支最终评审
- [ ] review-package → 子代理评审（携 Review Focus 与台账）→ 分级修复
  （Critical/Important RED→GREEN）→ minors 记台账 → 合并 master → push

## 执行顺序与依赖

S1（T1→T2）→ S2（T3→T4→T5→T6→T7）→ S3（T8-T10 可与 S2 并行，本计划串行）→
S4。T2 依赖 T1；T4 依赖 T3；T5/T6 依赖 T4；T7 依赖 T5/T6；T11 依赖 T2/T7。

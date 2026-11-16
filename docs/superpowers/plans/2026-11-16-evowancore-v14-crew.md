# EvoWanCore V14 计划——crew：多 Agent 自进化票据团队（examples）

**Goal:** 多 Agent 系统，体现**探索**（wave 循环发现失败 gap）与**自进化**
（失败轨迹→技能沉淀→可量化变强）。平台零侵入（V13 红线），纯二次开发。
**零网络红线**：Fake 模型剧本驱动；真 27b 复跑留 Ruling。

## 业务场景：票据运营团队（Invoice Ops Crew）

报销票据批次处理，三角色：classify（报销类型分类）/ extract（字段抽取）/
audit（对账校验）。脏模式注入：`date_format`（2026/1/5）、`tax_rate`
（税率口径错）、`currency_symbol`（￥3,000 带符号千分位）——脏票据会让
无技能的 agent 走弯路（多次试错）甚至失败。

## 自进化闭环（DGM 风格，真内核件）

```
wave N: 批票据 solve（Fake 剧本：无技能→试错/失败；有技能→直通）
  → 失败/弯路轨迹 → 反射器产技能候选（SkillArtifact：归一化脚本）
  → SkillGate 合同测试（脏样本 in/out）+ 沙箱 → 注册能力目录
wave N+1: agent 经能力目录调用新技能 → 成功率↑ 步数↓
```

## Task 1: domain.py — 票据域 + 评分器
**Files:** `examples/src/examples/crew/domain.py`；Test `examples/tests/test_crew_domain.py`
- `Invoice`（商家/金额/日期/税率/报销类型）、`DIRTY_MODES` 注入、
  `grader(extracted, truth)`（字段级对照）→ 每票据 pass/fail + 错误模式标签
- [ ] RED：三种脏模式各自产错；干净票据直过
- [ ] GREEN → gates → Commit

## Task 2: solver.py — 单票据 solve（Fake 剧本步数可控）
**Files:** `solver.py`；Test 追加
- `CrewSolver(registry, gateway)`：submit 单票据任务；Fake 模型剧本：
  能力目录无归一化技能 → 先交脏值（grader 拒）再重试一次仍错（2-3 步弯路
  /失败）；有技能 → 首步调技能再交答案（直通）
- [ ] RED：无技能 = 失败且步数≥3；有技能 = 成功且步数==2
- [ ] GREEN → Commit

## Task 3: evolve.py — 失败轨迹 → 技能注册（真门禁）
**Files:** `evolve.py`；Test 追加
- 规则反射器：错误模式标签 → 对应 `SkillArtifact` 归一化脚本候选
  （date→`skill_normalize_date` 等）
- `evolve_from_failures(failures) -> 注册列表`：SkillGate 合同测试 +
  agent-evolve Sandbox 执行 + SkillRegistryBridge 注册（失败候选被拒）
- [ ] RED：date 失败 → normalize_date 技能注册且可调；垃圾候选被门禁拒
- [ ] GREEN → Commit

## Task 4: crew.py — wave 循环 + 指标
**Files:** `crew.py`；Test 追加
- `InvoiceOpsCrew.run_waves(batches, n_waves)`：每 wave solve 全批 →
  grader → 失败驱动 evolve → 下 wave；收集 `WaveMetrics`
  （success_rate/avg_steps/skills_count）
- [ ] RED：3 wave 断言——wave1 success_rate < 1.0；wave3 == 1.0；
  avg_steps 单调不升且 wave3 < wave1；skills 随 wave 增长
- [ ] GREEN → Commit

## Task 5: 多 Agent 编排 + e2e + 终评审 + 合并
- classify/extract/audit 三 AgentSpec + ReplanningOrchestrator 失败重派
  （按能力标签）；e2e：全链 3 wave 变强断言；终评审 → 合并 → push

## Ruling（非目标）
- 真 27b 复跑：Fake 剧本先行，真实模型验证另开实验（同 V11.5 模式）
- A2A 跨进程角色通信：单进程 Orchestrator 已表达多 Agent；A2A 待分布式需求

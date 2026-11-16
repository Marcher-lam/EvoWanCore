# EvoWanCore V15 计划——crew 四能力全景（学习/探索/编排全量补齐）

**Goal:** crew 从"编排+进化"补成四能力全景，每项增益可量化断言。
平台零侵入红线；零网络（Fake 剧本）。

## Task 1: learning.py — B 闭环记忆全链
**Files:** `examples/src/examples/crew/learning.py`；Test `examples/tests/test_crew_learning.py`
- `reflect_rules(failures) -> list[MemoryRecord]`：失败模式→祈使规则
  （content={"statement", "claim":{"action","params"}} → MANDATORY 渲染）
- `CrewMemory`：SqliteMemoryStore（NS_RULES）写入 VERIFIED+ACTIVE；
  retriever()/assembler() 供 runtime 接线
- make_solver_env(memory=...)：runtime 接 memory_retriever/context_assembler
- [ ] RED：写入→检索（kind 关键词命中）→渲染含 MANDATORY call
- [ ] GREEN → gates → Commit

## Task 2: 多脏票据 + 记忆感知剧本（学习增益量化）
**Files:** domain.py（复合脏 dirty_payloads）/ fake_model.py / solver.py
- 复合脏（date+currency 同时）：无记忆=技能1→半修提交被拒→技能2→提交
  （4 步 1 拒）；有记忆（MANDATORY 全调指令）→技能1→技能2→一次提交
  （3 步 0 拒）
- SolveResult 加 rejected_submits；WaveMetrics 加 total_rejected
- [ ] RED：对照断言——有记忆步数少且 0 被拒
- [ ] GREEN → Commit

## Task 3: 探索全量 — curriculum / gap_driven / counterexample
**Files:** `explore.py`（新）
- curriculum：wave1 干净+单脏；wave2+ 注入复合脏（难度递进）
- gap_driven：wave N 失败模式 → 同模式新票注入 wave N+1
- counterexample：per-mode 边界值（闰年日期/零额/大额）自动追加进
  技能合同 expected_outputs
- [ ] RED：边界反例进合同且被拒的技能连边界都锁住；gap 注入生效
- [ ] GREEN → Commit

## Task 4: 编排深化 — classify→extract→audit 三阶段真跑
**Files:** fake_model.py（角色剧本）/ solver.py（submit_classification/
  submit_audit 能力）/ crew.py（流水线 + audit 大额打回）
- classify（kind=classify）→ 预判 dirty_modes 传入 extract goal；
  audit（kind=audit）对大额票（≥阈值）复核，needs_review 打回
- 三阶段均经 orchestrator.orchestrate 选人
- [ ] RED：大额票被 audit 打回；分类预判进 extract；三角色各真执行
- [ ] GREEN → Commit

## Task 5: e2e 四能力全景 + 终评审 + 合并
- e2e：学习（记忆写入+弯路消失）/ 探索（课程+gap+反例）/ 编排（三角色
  流水线 + 打回）/ 进化（技能 0→N + 自愈）同链断言
- 平台零侵入红线（conftest）→ 子代理终评审 → 修复 → 合并 → push

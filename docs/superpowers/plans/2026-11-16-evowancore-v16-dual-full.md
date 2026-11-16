# EvoWanCore V16 计划——双应用各自四能力全景

**Goal:** docworkbench 补编排（北极星口径一字不动）；crew 补真模型（realrun）。
**纪律三件套；平台零侵入。**

## A 线：docworkbench 补编排（加法不破基准）

### Task 1: OrchestratedWorkbench——分派/并行/重派/审计
**Files:** `examples/src/examples/docworkbench/orchestrated.py`；Test `examples/tests/test_wb_orchestrated.py`
**Interfaces:**
- 三角色 AgentSpec：solver-extract（["wb:extract"]）/ solver-explore（["wb:explore"]）/
  auditor（["wb:audit"]）——**CapabilityCompositionPolicy 真选人**（平台件）
- `solve_batch(tasks)`：按 dimension 选 solver → **并行** solve（asyncio.gather）
  → 失败**重派**第二 solver（T23：每次 solve 从原始文档起，重派安全）
  → auditor 对失败 run 读事件流诊断（失败阶段分类，供学习反射器消费）
- 执行单元 = `app.solve`（不绕过——vision/derived/T23 语义即北极星保护层）；
  与平台 Orchestrator 的分工差异在 docstring 记录（其绑定裸 runtime，
  docworkbench 任务需 app 层包装，编排语义在分派层）
- [ ] RED：按 kind 选对 solver；并行（耗时<串行）；失败重派成功；auditor 诊断
- [ ] GREEN → gates → Commit

### Task 2: 北极星不动断言
- 原 solve 单任务路径与 experiment 串行路径回归（全量测试即证）+
  显式断言：OrchestratedWorkbench 不修改 app 状态机/derived 语义
- [ ] Commit

## B 线：crew 补真模型（V4 方法论：真实验抓管道缺陷）

### Task 3: crew vlm 参数化 + realrun_crew.py
**Files:** solver.py（vlm= 参数）；`realrun_crew.py`
- make_solver_env(vlm=None→CrewFakeModel)；realrun 用 LMStudioVLM(27b)
- realrun：curriculum 3 wave × 6 票 × 三阶段 pipeline + 学习/进化全链
- [ ] tsc——不适用；pytest 全量仍绿 → Commit

### Task 4: 跑真 27b + 按结果修
- [ ] realrun 3/3 wave 报告（成功率/步数/被拒/技能曲线）存档
- [ ] 真模型暴露的管道缺陷逐个修（V4 模式）→ Commit

## Task 5: 双应用四能力验收 + 终评审 + 合并 push
- 验收：docworkbench 编排指标 + 北极星回归；crew realrun 报告
- 子代理终评审 → 修复 → 合并 → push

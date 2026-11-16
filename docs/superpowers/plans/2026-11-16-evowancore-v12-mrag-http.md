# EvoWanCore V12 计划——MRAG 服务化（agent-sdk 端点 + TS 客户端）

**Goal:** `POST /mrag/query` 把 MRAG 从进程内能力升级为跨进程 API；
TS SDK 同步。**纪律三件套不变；pytest 零网络（Fake VLM/Embedding）。**

## Task 1: SDK 端点 POST /mrag/query
**Files:** `agent-sdk/src/agent_sdk/api.py` + `models.py`；Test `agent-sdk/tests/test_mrag_api.py`
**Interfaces:** 组件 `mrag`（MRAGApp；缺失 → 503，同 explore/memory 惯例）。
body：`{query, k?=4, mode?="answer"}`（answer=VLM 带引用生成 /
retrieve=仅检索省调用）→ `{answer, citations[], hits[]}`。
- [ ] RED：无组件 503；retrieve 模式 hits 齐；answer 模式引用正确
- [ ] GREEN → gates → Commit

## Task 2: TS 客户端 mragQuery
**Files:** `agent-sdk-ts/src/{types,index}.ts`
**Interfaces:** `mragQuery(query, {k?, mode?}) → MragQueryResponse` 类型化。
- [ ] tsc + 冒烟追加（带 MRAG 组件的 server 上打一条 retrieve）→ Commit

## Task 3: 验收 + 终评审 + 合并 + push

# EvoWanCore V11 计划——多模态 RAG（examples/MRAG）

**Goal:** 基于本项目资产（能力目录 / VLM 适配 / sqlite 惯例）开发多模态 RAG，
**不是孤立脚本**：ingest/query 注册为 Capability，可被 AgentRuntime 调用。
**纪律三件套不变；零网络测试红线**（Fake embedding/VLM；真实 LM Studio 路径
与 docworkbench 同模式，不在 pytest 打网络）。

**非目标（Ruling）**：PDF/Office 解析（重依赖）——ingest 输入为"页面图像 +
可选全文"，与 docworkbench corpus 管道天然衔接；向量数据库——sqlite + 余弦
够用，规模化再换；重排序器——V11 不做。

## 架构

```
SourceDoc(name, pages=[(page_no, png_bytes)], text?)
  → ingest: VLM 逐页描述 → chunks(text 块 + 页描述块) → embed → store
  → retrieve(query, k): 余弦 top-k → [(chunk, page_image, score)]
  → answer: 组装文本块+页面图像 → VLM → 带引用答案 [doc p.N]
  → capabilities: mrag_query(read) / mrag_ingest(write)
```

## Task 1: embed.py — EmbeddingProvider
**Files:** `examples/src/examples/mrag/embed.py`；Test `examples/tests/test_mrag_embed.py`
**Interfaces:** `EmbeddingProvider` 协议（`async embed(texts: list[str]) -> list[list[float]]`）；
`HashEmbedding(dim=64)`（确定性 SHA 分桶→单位向量，零依赖测试/兜底）；
`LmStudioEmbedding(base_url, model)`（/v1/embeddings 真实路径）。
- [ ] RED：确定性（同文同向量）/ 不同文不同向量 / 单位范数 / 批量
- [ ] GREEN → gates → Commit

## Task 2: store.py — MRAGStore（索引 + 多模态检索）
**Files:** `examples/src/examples/mrag/store.py`；Test `examples/tests/test_mrag_store.py`
**Interfaces:** `MRAGStore(db_path, blobs_dir, embedder)`——
`index(doc: SourceDoc, page_descriptions: list[str])`（文本切块 + 页描述块 +
图像落盘）；`retrieve(query: str, k=4)` → `[RetrievedChunk]`（content/score/
doc/page/page_image_path——多模态证据即页面图像引用）。
- [ ] RED：检索命中正确块（汉明距离意义下）；top-k 排序；页图像路径存在
- [ ] GREEN → gates → Commit

## Task 3: ingest.py — VLM 页面描述切块
**Files:** `examples/src/examples/mrag/ingest.py`；Test 追加
**Interfaces:** `PageDescriber(vlm_adapter).describe(png_bytes) -> str`（VLM
提示词：完整转录文本+表格结构+图形要点）；`ingest(store, doc, describer)`
→ 返回 chunk 数。
- [ ] RED：FakeVLM 断言提示词含图像 data URL；页描述成块入库
- [ ] GREEN → gates → Commit

## Task 4: answer.py + capabilities.py
**Files:** `examples/src/examples/mrag/answer.py` / `capabilities.py`；Test 追加
**Interfaces:** `VLMAnswerer(vlm).answer(query, retrieved) -> Answer(citations)`
——prompt 组装检索块 + 首命中页面图像，要求带 `[doc p.N]` 引用；
`register_mrag_tools(registry, store, answerer)`：`mrag_query`（read）/
`mrag_ingest`（write）——副作用如实声明红线。
- [ ] RED：FakeVLM 断言 prompt 含块文本与图像；引用解析；能力红线
- [ ] GREEN → gates → Commit

## Task 5: app.py e2e + 终评审 + 合并
**Files:** `examples/src/examples/mrag/app.py`（MRAGApp 一条链组装）；
`examples/tests/test_mrag_e2e.py`
- [ ] e2e：3 页图文 mini corpus（FakeVLM/HashEmbedding）→ ingest → query →
  答案引用页码正确
- [ ] 全量门禁 → 子代理终评审 → 修复 → 合并 → push

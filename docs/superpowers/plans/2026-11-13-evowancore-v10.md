# EvoWanCore V10 计划——工程面收口（CI / 鉴权 / 文档 / 发布就绪）

**Goal:** 从未立项的工程面一次收口；A2A Notification 已于 V9 交付（不重做）。
**纪律三件套不变。**发布为外向动作：本版只做**就绪**（CI 手动触发 job + 打包配置），
不实际 publish（无凭据不越权）。

## Task 1: CI 流水线（GitHub Actions）
**Files:** `.github/workflows/ci.yml`
- job `python`（ubuntu-latest, py3.12）：uv sync（含 browser extra）→
  playwright install chromium --with-deps → ruff check → 全量 pytest
- job `ts`（ubuntu-latest, node 22）：npm ci → tsc build → smoke（自起 Python
  server，需 uv 就绪）
- 缓存：uv / playwright browsers / npm；触发 push master + PR
- [ ] 本地等效命令全绿 → Commit

## Task 2: SDK server bearer 鉴权
**Files:** `agent-sdk/src/agent_sdk/api.py`；Test 追加；TS client `authToken`
**Interfaces:** `AgentSDKServer(factory, auth_token=None)`——设置后全部端点要求
`Authorization: Bearer <token>`（hmac 恒时比较，401 + WWW-Authenticate）；
默认 None=开放（回环兼容）。TS `ClientOptions.authToken`。
- [ ] RED：无/错 token 401；对 token 全端点通；默认开放不回归
- [ ] GREEN → gates → Commit

## Task 3: HANDOVER.md 重写（V1→V9 全景）
**Files:** `docs/HANDOVER.md`
- 包清单与职责、协议实现对照（reserved 4 桩 → 模块）、北极星指标、
  如何跑（pytest / TS smoke / playwright extra / experiments）、
  安全红线（key 不入仓 / 副作用如实声明 / 沙箱）、Ruling 索引（v5-v9 计划）
- [ ] Commit

## Task 4: 发布就绪 + 验收 + 终评审 + 合并 + push
- `.github/workflows/release.yml`（workflow_dispatch 手动：python build+twin
  check / npm pack 检查，publish 步骤留注释占位——需凭据）
- 验收：CI yaml 语法（actionlint 或等效本地校验）+ 鉴权 e2e + smoke
- 终评审 → 修复 → 合并 → push

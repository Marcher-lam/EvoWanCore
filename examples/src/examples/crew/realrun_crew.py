"""crew 真实链路验证（V16-T4）——27b 全链：三阶段流水线 + 学习 + 进化。

与 Fake 剧本的差异即实验价值：真模型不按剧本来——classify 预判准确性、
技能/记忆遵循率、失败模式稳定性都待实测（V4 方法论：真实验抓管道缺陷）。
运行：uv run python -m examples.crew.realrun_crew [--waves N] [--pipeline]
报告：examples/src/examples/crew/realrun_crew_report.md
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BASE = Path(__file__).parent
VLM_MODEL = "qwen3.8-27b-mlx"

_BATCH_MODES: list[list[str | None]] = [
    ["date_format"], ["currency_symbol"], ["tax_rate"],
    ["date_format"], [None], ["currency_symbol"],
]


def _batch() -> list:
    from examples.crew.domain import Invoice, dirty_payloads

    out = []
    for i, modes in enumerate(_BATCH_MODES):
        inv = Invoice(
            invoice_id=f"real-{i}", vendor=f"供应商{i}",
            amount=100.0 * (i + 1), date="2026-10-18",
            tax_rate=0.06, category="hardware",
        )
        payload = inv.model_dump()
        for m in modes:
            if m is None:
                continue
            d = dirty_payloads(inv, [m])
            payload.update(d)
        out.append((inv, payload))
    return out


async def main() -> int:

    from examples.crew.crew import make_crew
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env
    from examples.crew.vlm_compat import UserRoleGuard
    from examples.docworkbench.lmstudio_vlm import LMStudioVLM

    waves = 3
    if "--waves" in sys.argv:
        waves = int(sys.argv[sys.argv.index("--waves") + 1])
    use_pipeline = "--pipeline" in sys.argv
    tool_first = "--tool-first" in sys.argv
    probe = "--probe" in sys.argv  # V18 反饥饿探索（frontier+边界探针）

    # UserRoleGuard：qwen3.8 模板要求 user 角色（T4 实测 400 修复）
    vlm = UserRoleGuard(LMStudioVLM(VLM_MODEL))
    tmp = _BASE / "realrun_ws"
    tmp.mkdir(exist_ok=True)
    # 跨 run 记忆清场：残 DB 会把上一 run 的规则带进对照（变量失控）
    mem_db = tmp / "crew_mem.db"
    mem_db.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(mem_db) + suffix).unlink(missing_ok=True)
    mem = CrewMemory(mem_db)
    env = make_solver_env(
        tmp, memory=mem, vlm=vlm, max_steps=12,
    )
    crew = make_crew(
        env, workspace=tmp / "skills", memory=mem, tool_first=tool_first,
    )

    batch = _batch()
    print(f"[realrun] 27b={VLM_MODEL} waves={waves} "
          f"pipeline={use_pipeline} tool_first={tool_first} probe={probe}")
    print(f"[realrun] batch={len(batch)} 票；ingest 学习/进化全链……")

    metrics = await crew.run_waves(
        batch, n_waves=waves, pipeline=use_pipeline, probe=probe,
    )
    rules = await mem.store.query("rules", {})

    lines = [
        "# crew 真实链路报告（27b）",
        "",
        f"- VLM: `{VLM_MODEL}`（reasoning_effort=low）",
        f"- waves: {waves}；pipeline: {use_pipeline}；"
        f"tool_first: {tool_first}；probe: {probe}；批: {len(batch)} 票",
        f"- 记忆规则数（run 末）: {len(rules)}",
        "",
        "| wave | 成功率 | 平均步数 | 被拒 | 技能 | 自愈 | 需复核 "
        "| 探针 | 探针失败 | 探针自愈 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in metrics:
        probe_fail = sum(m.probe_failures_by_mode.values())
        lines.append(
            f"| {m.wave} | {m.success_rate:.2f} | {m.avg_steps:.1f} "
            f"| {m.total_rejected} | {m.skills} | {m.recovered} "
            f"| {m.needs_review_count} | {m.probe_total} "
            f"| {probe_fail}{m.probe_failures_by_mode or ''} "
            f"| {m.probe_recovered} |"
        )
        print(
            f"[wave {m.wave}] rate={m.success_rate:.2f} "
            f"steps={m.avg_steps:.1f} rejected={m.total_rejected} "
            f"skills={m.skills} recovered={m.recovered} "
            f"probe={m.probe_total}/{probe_fail}/{m.probe_recovered}"
        )
    print(f"[memory] rules={len(rules)}")
    report = _BASE / "realrun_crew_report.md"
    report.write_text("\n".join(lines) + "\n")
    print(f"[report] {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

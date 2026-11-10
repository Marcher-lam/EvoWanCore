"""run_acceptance — 程序化验收（spec §5.7）：纯 Python 规则检查，全自动。"""

from __future__ import annotations

from pydantic import BaseModel

from examples.docworkbench.corpus import Corpus


class AcceptanceReport(BaseModel):
    passed: bool
    checks: list[dict] = []


def run_acceptance(task_id: str, result: dict, corpus: Corpus) -> AcceptanceReport:
    script = corpus.acceptance_scripts.get(task_id)
    if script is None:
        return AcceptanceReport(
            passed=False, checks=[{"check": "task_exists", "detail": f"任务 {task_id} 无验收脚本"}]
        )
    outcome = script(result)
    return AcceptanceReport(passed=bool(outcome["passed"]), checks=outcome["checks"])

from examples.docworkbench.audit import AuditReport  # noqa: F401,E402 — 再导出便于单入口导入

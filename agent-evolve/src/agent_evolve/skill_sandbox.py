"""SkillSandbox — 受治理技能脚本的子进程执行器（spec §6）。

候选代码一律子进程执行：进程组隔离、超时击杀、输出截断。
发布前提：contract_test 通过（expected_outputs + known_failure_scenarios）。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

MAX_STDOUT_BYTES = 1024 * 1024


class SkillArtifact(BaseModel):
    skill_id: str
    version: str
    description: str = ""
    preconditions: dict = {}
    entrypoint: str
    expected_outputs: dict = {}
    known_failure_scenarios: list[dict] = []
    script_files: dict[str, str]


class SkillRunResult(BaseModel):
    status: Literal["succeeded", "failed"]
    stdout: str = ""
    stderr: str = ""
    artifacts: list[str] = []
    duration: float = 0.0
    truncated: bool = False
    pid: int | None = None
    error: dict | None = None


class ContractTestReport(BaseModel):
    passed: bool
    cases: list[dict]


class SkillSandbox:
    def __init__(self, base_dir: Path | str = "skill_workspace") -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _materialize(self, artifact: SkillArtifact) -> Path:
        ws = self._base / artifact.skill_id / artifact.version
        ws.mkdir(parents=True, exist_ok=True)
        for name, content in artifact.script_files.items():
            (ws / name).write_text(content)
        return ws / artifact.entrypoint

    async def run(
        self, artifact: SkillArtifact, args: dict, *, timeout_s: float = 30
    ) -> SkillRunResult:
        entry = self._materialize(artifact)
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(entry),
            json.dumps(args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # 独立进程组，便于整组击杀
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            self._kill_group(proc)
            await proc.wait()  # 收割，不留僵尸
            return SkillRunResult(
                status="failed",
                duration=time.monotonic() - t0,
                pid=proc.pid,
                error={"reason": f"timeout after {timeout_s}s", "kind": "timeout"},
            )
        duration = time.monotonic() - t0
        truncated = len(stdout_b) > MAX_STDOUT_BYTES
        stdout = stdout_b[:MAX_STDOUT_BYTES].decode(errors="replace")
        stderr = stderr_b[:MAX_STDOUT_BYTES].decode(errors="replace")
        if proc.returncode != 0:
            return SkillRunResult(
                status="failed",
                stdout=stdout,
                stderr=stderr,
                duration=duration,
                pid=proc.pid,
                error={"reason": f"exit code {proc.returncode}", "kind": "exit"},
            )
        return SkillRunResult(
            status="succeeded",
            stdout=stdout,
            stderr=stderr,
            duration=duration,
            truncated=truncated,
            pid=proc.pid,
        )

    @staticmethod
    def _kill_group(proc: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()

    async def contract_test(
        self, artifact: SkillArtifact, *, timeout_s: float = 30
    ) -> ContractTestReport:
        cases: list[dict] = []

        for name, spec in artifact.expected_outputs.items():
            result = await self.run(artifact, spec.get("args", {}), timeout_s=timeout_s)
            ok = result.status == "succeeded"
            if ok and "expect_stdout_contains" in spec:
                ok = spec["expect_stdout_contains"] in result.stdout
            cases.append(
                {"name": name, "kind": "expected_output", "ok": ok, "status": result.status}
            )

        for scenario in artifact.known_failure_scenarios:
            name = scenario.get("name", "unnamed")
            result = await self.run(artifact, scenario.get("args", {}), timeout_s=timeout_s)
            expect_failed = scenario.get("expect_failed", True)
            ok = (result.status == "failed") == expect_failed
            if ok and "expect_stderr_contains" in scenario:
                ok = scenario["expect_stderr_contains"] in result.stderr
            cases.append(
                {"name": name, "kind": "failure_scenario", "ok": ok, "status": result.status}
            )

        return ContractTestReport(passed=all(c["ok"] for c in cases), cases=cases)

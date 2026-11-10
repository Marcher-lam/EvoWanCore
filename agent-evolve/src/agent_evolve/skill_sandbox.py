"""SkillSandbox — 受治理技能脚本的子进程执行器（spec §6）。

候选代码一律子进程执行：进程组隔离、超时击杀、输出截断。
发布前提：contract_test 通过（expected_outputs + known_failure_scenarios）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

MAX_STDOUT_BYTES = 1024 * 1024

# A#1：macOS sandbox-exec 网络 deny profile——其余默认放行，仅网络全拒
_NETWORK_DENY_PROFILE = "(version 1)(allow default)(deny network*)"


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


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _make_preexec(memory_limit_mb: int | None, cpu_limit_s: bool):
    """I6: rlimit 资源限制（unix 尽力而为；Windows 退化为无限制）。"""
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return None
    if memory_limit_mb is None and not cpu_limit_s:
        return None

    def _apply() -> None:
        try:
            if memory_limit_mb is not None:
                lim = memory_limit_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
            if cpu_limit_s:
                soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
                resource.setrlimit(resource.RLIMIT_CPU, (max(soft, 1), hard))
        except (ValueError, OSError):  # 平台不支持时降级为不限制
            pass

    return _apply


class SkillSandbox:
    def __init__(
        self,
        base_dir: Path | str = "skill_workspace",
        *,
        memory_limit_mb: int | None = None,
        cpu_limit_s: bool = False,
        network: Literal["deny", "allow"] = "deny",
    ) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._memory_limit_mb = memory_limit_mb
        self._cpu_limit_s = cpu_limit_s
        self._network = network
        if network == "deny" and sys.platform not in ("darwin", "linux"):
            raise ValueError(
                f"网络隔离不支持平台 {sys.platform}：显式报错，不静默跳过"
                "（如需无隔离运行请显式传 network='allow'）"
            )

    def _wrap_argv(self, argv: list[str]) -> list[str]:
        """A#1：network=deny 的平台包装器（macOS sandbox-exec / Linux unshare -n）。"""
        if self._network != "deny":
            return argv
        if sys.platform == "darwin":
            return ["/usr/bin/sandbox-exec", "-p", _NETWORK_DENY_PROFILE, *argv]
        return ["unshare", "--net", *argv]

    @staticmethod
    def _safe_component(value: str, what: str) -> str:
        if not _SAFE_ID.match(value):
            raise ValueError(f"非法 {what}: {value!r}（仅允许 [A-Za-z0-9._-]）")
        return value

    def _materialize(self, artifact: SkillArtifact) -> Path:
        # C1 防护：skill_id/version 只能是安全单段；文件名/entrypoint 不得绝对路径、
        # 不得含 ..、必须落在工作区内（resolve + is_relative_to 双保险）
        self._safe_component(artifact.skill_id, "skill_id")
        self._safe_component(artifact.version, "version")
        ws = (self._base / artifact.skill_id / artifact.version).resolve()
        ws.mkdir(parents=True, exist_ok=True)

        def _resolve_under_ws(name: str, what: str) -> Path:
            candidate = (ws / name).resolve()
            if candidate == ws or not candidate.is_relative_to(ws):
                raise ValueError(f"非法 {what}: {name!r}（不得逃出工作区）")
            return candidate

        for name in artifact.script_files:
            _resolve_under_ws(name, "文件名")
        entry = _resolve_under_ws(artifact.entrypoint, "entrypoint")
        for name, content in artifact.script_files.items():
            _resolve_under_ws(name, "文件名").write_text(content)
        return entry

    async def run(
        self, artifact: SkillArtifact, args: dict, *, timeout_s: float = 30
    ) -> SkillRunResult:
        entry = self._materialize(artifact)
        t0 = time.monotonic()
        preexec = _make_preexec(self._memory_limit_mb, self._cpu_limit_s)
        proc = await asyncio.create_subprocess_exec(
            *self._wrap_argv([sys.executable, str(entry), json.dumps(args)]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # 独立进程组，便于整组击杀
            **({"preexec_fn": preexec} if preexec is not None else {}),
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

        # M10：expected_outputs 与 failure_scenarios 皆空 → 不得 vacuous pass
        if not cases:
            cases.append({"name": "_empty_contract", "kind": "vacuous_guard", "ok": False})
        return ContractTestReport(passed=all(c["ok"] for c in cases), cases=cases)

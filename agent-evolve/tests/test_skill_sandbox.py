"""T16 技能子进程沙箱：超时回收、输出截断、契约测试门禁。"""

import os
import signal
import time

import pytest
from agent_evolve.skill_sandbox import ContractTestReport, SkillArtifact, SkillSandbox


def _artifact(files: dict[str, str], **over) -> SkillArtifact:
    base = dict(
        skill_id="skill_echo",
        version="1.0.0",
        description="回显参数",
        preconditions={"python": ">=3.11"},
        entrypoint="main.py",
        expected_outputs={},
        known_failure_scenarios=[],
        script_files=files,
    )
    base.update(over)
    return SkillArtifact(**base)


ECHO_SCRIPT = (
    "import json, sys\n"
    "args = json.loads(sys.argv[1])\n"
    "print(json.dumps({'echo': args}))\n"
)


async def test_run_happy_path(tmp_path) -> None:
    sandbox = SkillSandbox(base_dir=tmp_path / "ws")
    result = await sandbox.run(_artifact({"main.py": ECHO_SCRIPT}), {"k": "v"}, timeout_s=10)
    assert result.status == "succeeded"
    assert '"echo"' in result.stdout
    assert result.duration < 10
    assert result.truncated is False


async def test_timeout_kills_and_reports_failed(tmp_path) -> None:
    loop_script = "import time\nprint('started', flush=True)\ntime.sleep(600)\n"
    sandbox = SkillSandbox(base_dir=tmp_path / "ws")
    t0 = time.monotonic()
    result = await sandbox.run(_artifact({"main.py": loop_script}), {}, timeout_s=0.5)
    elapsed = time.monotonic() - t0
    assert elapsed < 5, "沙箱不得挂起等待死循环"
    assert result.status == "failed"
    assert "timeout" in str(result.error).lower()
    # 进程组被回收：pid 不再存活
    if result.pid is not None:
        with pytest.raises(ProcessLookupError):
            os.kill(result.pid, signal.SIGKILL)  # noqa: protect against zombie process groups


async def test_stdout_truncated(tmp_path) -> None:
    big = "print('x' * (2 * 1024 * 1024))\n"
    sandbox = SkillSandbox(base_dir=tmp_path / "ws")
    result = await sandbox.run(_artifact({"main.py": big}), {}, timeout_s=15)
    assert result.truncated is True
    assert len(result.stdout) <= 1024 * 1024 + 1


async def test_exit_error_captured(tmp_path) -> None:
    boom = "import sys\nsys.stderr.write('bad input')\nsys.exit(3)\n"
    sandbox = SkillSandbox(base_dir=tmp_path / "ws")
    result = await sandbox.run(_artifact({"main.py": boom}), {}, timeout_s=10)
    assert result.status == "failed"
    assert "bad input" in result.stderr


async def test_contract_test_gates_publication(tmp_path) -> None:
    sandbox = SkillSandbox(base_dir=tmp_path / "ws")

    good = _artifact(
        {"main.py": ECHO_SCRIPT},
        expected_outputs={
            "echo_case": {"args": {"a": 1}, "expect_stdout_contains": '"echo"'},
        },
        known_failure_scenarios=[
            {
                "name": "invalid_json_arg",
                "args": {},  # ECHO_SCRIPT 不会失败 → 这个场景应当失败？不：
                # 场景语义 = 技能应优雅失败的场景。改用 boom 分支验证。
                "expect_failed": False,
            },
        ],
    )
    report = await sandbox.contract_test(good)
    assert isinstance(report, ContractTestReport)
    assert report.passed is True

    # 期望落空（stdout 不含 MAGIC）→ 门禁不通过
    broken = _artifact(
        {"main.py": ECHO_SCRIPT},
        expected_outputs={
            "magic_case": {"args": {}, "expect_stdout_contains": "MAGIC"},
        },
    )
    report2 = await sandbox.contract_test(broken)
    assert report2.passed is False
    assert any(not c["ok"] for c in report2.cases)

    # 应失败的场景实际成功 → 同样不通过
    should_fail = _artifact(
        {"main.py": ECHO_SCRIPT},
        known_failure_scenarios=[
            {"name": "must_fail", "args": {}, "expect_failed": True},
        ],
    )
    report3 = await sandbox.contract_test(should_fail)
    assert report3.passed is False


async def test_workspace_path_traversal_rejected(tmp_path) -> None:
    """C1: LLM 生成的 skill_id/文件名不得逃出工作区（绝对路径与 ../ 都拒绝）。"""
    sandbox = SkillSandbox(base_dir=tmp_path / "ws")

    with pytest.raises(ValueError, match="skill_id"):
        await sandbox.run(_artifact({"main.py": "print(1)"}, skill_id="../escape"), {})
    with pytest.raises(ValueError, match="version"):
        await sandbox.run(_artifact({"main.py": "print(1)"}, version="../../v"), {})

    with pytest.raises(ValueError, match="文件名"):
        await sandbox.run(_artifact({"../../../evil.py": "print(1)"}, skill_id="ok"), {})
    with pytest.raises(ValueError, match="文件名"):
        await sandbox.run(_artifact({"/tmp/evil_abs.py": "print(1)"}, skill_id="ok"), {})
    with pytest.raises(ValueError, match="entrypoint"):
        await sandbox.run(_artifact({"main.py": "print(1)"}, entrypoint="/tmp/evil.py"), {})

    # 正常技能不受影响
    result = await sandbox.run(_artifact({"main.py": ECHO_SCRIPT}), {"a": 1})
    assert result.status == "succeeded"


async def test_resource_limits_do_not_break_normal_skills(tmp_path) -> None:
    """I6: rlimit(内存/CPU) 配置后正常技能照常运行。"""
    sandbox = SkillSandbox(base_dir=tmp_path / "ws", memory_limit_mb=256, cpu_limit_s=True)
    result = await sandbox.run(_artifact({"main.py": ECHO_SCRIPT}), {"k": 1}, timeout_s=10)
    assert result.status == "succeeded"
    # 内存吞噬脚本：在支持的平台上被 rlimit 掐死或超时击杀，总之不得挂起宿主
    hog = "x = []\nwhile True:\n    x.append(b'A' * 1024 * 1024)\n"
    result2 = await sandbox.run(_artifact({"main.py": hog}), {}, timeout_s=3)
    assert result2.status == "failed"


async def test_empty_contract_fails(tmp_path) -> None:
    """M10：expected_outputs 与 failure_scenarios 皆空 → 不得 vacuous pass。"""
    from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox

    sandbox = SkillSandbox(tmp_path / "ws")
    artifact = SkillArtifact(
        skill_id="s_empty",
        version="1",
        entrypoint="main.py",
        script_files={"main.py": "print('hi')"},
        expected_outputs={},
        known_failure_scenarios=[],
    )
    report = await sandbox.contract_test(artifact)
    assert report.passed is False, "空契约必须 fail（无验收即无发布资格）"


# ---------- T9（A#1）：沙箱网络隔离 ----------


def _net_probe(port: int) -> SkillArtifact:
    script = (
        "import socket\n"
        f"s = socket.create_connection(('127.0.0.1', {port}), timeout=3)\n"
        "print('CONNECTED')\n"
    )
    return SkillArtifact(
        skill_id="s_net_probe",
        version="1",
        entrypoint="probe.py",
        script_files={"probe.py": script},
    )


async def test_network_deny_blocks_socket(tmp_path) -> None:
    """network=deny（默认）：对 127.0.0.1 已监听端口的出站连接也被阻断（unix 实测）。"""
    import socket as sockmod

    server = sockmod.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        sandbox = SkillSandbox(tmp_path / "ws_deny")
        result = await sandbox.run(_net_probe(port), {})
        assert result.status == "failed", "deny 下连本机监听端口也应失败"
    finally:
        server.close()


async def test_network_allow_permits(tmp_path) -> None:
    """network=allow：同一探针可连通本机监听端口。"""
    import socket as sockmod

    server = sockmod.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        sandbox = SkillSandbox(tmp_path / "ws_allow", network="allow")
        result = await sandbox.run(_net_probe(port), {})
        assert result.status == "succeeded"
        assert "CONNECTED" in result.stdout
    finally:
        server.close()


async def test_unsupported_platform_raises(tmp_path, monkeypatch) -> None:
    """平台不支持（如 win32）+ deny → 显式 ValueError，不静默跳过。"""
    monkeypatch.setattr("sys.platform", "win32")
    with pytest.raises(ValueError, match="网络隔离"):
        SkillSandbox(tmp_path / "ws_x", network="deny")


async def test_contract_accepts_substring_lists(tmp_path) -> None:
    """真模型缺陷（DeepSeek 实测）：expect_stdout_contains 声明成 list——契约消费端
    须接受 str | list[str]（列表=全部子串都须命中）。"""
    from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox

    sandbox = SkillSandbox(tmp_path / "ws")
    artifact = SkillArtifact(
        skill_id="s_list", version="1", entrypoint="main.py",
        expected_outputs={
            "ok": {"args": {}, "expect_stdout_contains": ["lossless", '"dpi": 150']},
        },
        script_files={"main.py": (
            "import json\nprint(json.dumps({'lossless': True, 'dpi': 150}))\n")},
    )
    report = await sandbox.contract_test(artifact)
    assert report.passed, "列表声明（全命中）应通过"

    partial = artifact.model_copy(deep=True)
    partial.expected_outputs["ok"]["expect_stdout_contains"] = ["lossless", "absent-substring"]
    report2 = await sandbox.contract_test(partial)
    assert not report2.passed, "列表任一未命中 → 挂"

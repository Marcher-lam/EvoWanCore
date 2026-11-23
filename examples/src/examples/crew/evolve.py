"""crew evolve（V14-T3）——失败轨迹 → 归一化技能 → 真门禁 → 注册。

门禁走 agent-evolve 真家伙：SkillSandbox.contract_test（合同：声明行为
必须实测达成）+ 沙箱执行 + SkillRegistryBridge 版本化注册。
Ruling：不做 DualRunner 父子对比（Fake 剧本世界无真对照意义）；
contract_test 即 evaluate_skill 的第一道真门。
"""

from __future__ import annotations

from pathlib import Path

from agent_evolve.skill_registry import SkillRegistryBridge
from agent_evolve.skill_sandbox import SkillArtifact, SkillSandbox

# 每种脏模式 → 归一化技能工件（脚本 = 真 Python，沙箱执行）
_SKILL_SCRIPTS: dict[str, dict[str, str]] = {
    "date_format": {
        "main.py": (
            "import json, sys\n"
            "args = json.loads(sys.argv[1])\n"
            "p = str(args.get('value', '')).replace('-', '/').split('/')\n"
            "print(json.dumps({'normalized': "
            "f'{int(p[0]):04d}-{int(p[1]):02d}-{int(p[2]):02d}'}))\n"
        ),
    },
    "currency_symbol": {
        "main.py": (
            "import json, sys\n"
            "args = json.loads(sys.argv[1])\n"
            "raw = str(args.get('value', '')).replace('\\uffe5', '')"
            ".replace(',', '')\n"
            "print(json.dumps({'normalized': float(raw)}))\n"
        ),
    },
    "tax_rate": {
        "main.py": (
            "import json, sys\n"
            "args = json.loads(sys.argv[1])\n"
            "# 一般纳税人硬件采购口径 6%（脏 3% 为口径错）\n"
            "print(json.dumps({'normalized': 0.06}))\n"
        ),
    },
    # V18 frontier：大写中文金额解析（真解析器——万/亿分段 + 隐式壹 + 零额；
    # 终评审 P1-1 修复：亿+万复合不再重复乘累计 total；垃圾输入退出非零）
    "amount_words": {
        "main.py": (
            "import json, sys\n"
            "args = json.loads(sys.argv[1])\n"
            "s = str(args.get('value', '')).replace('元整', '')"
            ".replace('元', '')\n"
            "DIG = {'零': 0, '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5,"
            " '陆': 6, '柒': 7, '捌': 8, '玖': 9}\n"
            "UNIT = {'拾': 10, '佰': 100, '仟': 1000}\n"
            "total = section = num = 0\n"
            "seen = False\n"
            "for ch in s:\n"
            "    if ch in DIG:\n"
            "        num = DIG[ch]\n"
            "        seen = True\n"
            "    elif ch in UNIT:\n"
            "        section += (num or 1) * UNIT[ch]\n"
            "        num = 0\n"
            "        seen = True\n"
            "    elif ch in ('万', '亿'):\n"
            "        total = total + (section + num) "
            "* (10000 if ch == '万' else 100000000)\n"
            "        section = num = 0\n"
            "        seen = True\n"
            "if not seen:\n"
            "    sys.exit(1)\n"
            "print(json.dumps({'normalized': float(total + section + num)}))\n"
        ),
    },
}

# 合同：声明行为（脏样本 in → 期望 stdout 子串）
_SKILL_CONTRACTS: dict[str, dict] = {
    # 多样本合同（终评审 H2：单样本会放行"只对合同样本正确"的假技能）
    "date_format": {
        "case1": {"args": {"value": "2026/10/18"},
                  "expect_stdout_contains": '"normalized": "2026-10-18"'},
        "case2": {"args": {"value": "2025/1/5"},
                  "expect_stdout_contains": '"normalized": "2025-01-05"'},
        "case3": {"args": {"value": "2026/12/31"},
                  "expect_stdout_contains": '"normalized": "2026-12-31"'},
    },
    "currency_symbol": {
        "case1": {"args": {"value": "￥385,000"},
                  "expect_stdout_contains": '"normalized": 385000.0'},
        "case2": {"args": {"value": "￥12,345"},
                  "expect_stdout_contains": '"normalized": 12345.0'},
    },
    "tax_rate": {
        "case1": {"args": {"value": "0.03"},
                  "expect_stdout_contains": '"normalized": 0.06'},
        "case2": {"args": {"value": "anything"},
                  "expect_stdout_contains": '"normalized": 0.06'},
    },
    "amount_words": {
        "case1": {"args": {"value": "叁万捌仟伍佰元整"},
                  "expect_stdout_contains": '"normalized": 38500.0'},
        "case2": {"args": {"value": "贰佰零伍元整"},
                  "expect_stdout_contains": '"normalized": 205.0'},
        "case3": {"args": {"value": "拾贰元整"},
                  "expect_stdout_contains": '"normalized": 12.0'},
        # 终评审 P1-2：锁亿+万复合（修复前该样本被真门禁拒绝）
        "case4": {"args": {"value": "壹亿贰仟叁佰肆拾伍万元整"},
                  "expect_stdout_contains": '"normalized": 123450000.0'},
    },
}

_SKILL_IDS = {
    "date_format": "normalize_date",
    "currency_symbol": "normalize_amount",
    "tax_rate": "correct_tax_rate",
    "amount_words": "normalize_amount_words",
}


def build_artifact(mode: str, *, version: str = "0.1.0",
                   scripts: dict[str, str] | None = None) -> SkillArtifact:
    return SkillArtifact(
        skill_id=_SKILL_IDS[mode], version=version,
        description=f"归一化 {mode} 脏模式",
        entrypoint="main.py",
        expected_outputs=_SKILL_CONTRACTS[mode],
        script_files=scripts or _SKILL_SCRIPTS[mode],
    )


async def register_skill_for_mode(
    env, mode: str, *, workspace: Path, generation: int = 1,
    scripts: dict[str, str] | None = None,
) -> bool:
    """失败模式 → 技能候选 → 多样本合同门禁 → 沙箱注册进能力目录。

    返回 False = 候选被门禁拒绝（合同不符——绝不带病注册）。
    workspace 必传（CWD 默认会往仓库写产物——终评审 H1 实证）；
    version 从 generation 派生（重复注册递增，可审计可回滚）。
    """
    artifact = build_artifact(
        mode, version=f"0.{generation}.0", scripts=scripts,
    )
    # 探索反例：边界值追加进合同（闰年/零额等——锁住边界作弊）
    from examples.crew.explore import counterexample_cases

    artifact = artifact.model_copy(update={
        "expected_outputs": {
            **artifact.expected_outputs, **counterexample_cases(mode),
        },
    })
    sandbox = SkillSandbox(workspace, network="deny")
    contract = await sandbox.contract_test(artifact)
    if not contract.passed:
        return False
    bridge = SkillRegistryBridge(sandbox=sandbox, registry=env.registry)
    descriptor = bridge.register(artifact)

    # V17 真跑实测：tool-first 引导后 27b 真调技能——但平台 bridge 的
    # handler 不容错 str arguments（27b JSON 字符串怪癖全工具一致）。
    # V18 终评审日志重放：平台 bridge 还把 input_schema 硬编码成
    # doc_id/dpi → 真 27b 按 advertised schema 发参，技能 120/120 全灭
    # （Fake 不看 schema 故测试全绿——Fake-绿掩真-红的典型）。
    # 应用层零侵入修正：重绑 schema 正确的描述符副本（含 str 容错）。
    inner = env.registry.handler_for(descriptor.id)

    async def _str_tolerant(args):
        import json as _json

        value = args
        for _ in range(2):
            if isinstance(value, str):
                try:
                    value = _json.loads(value)
                except ValueError:
                    return {"error": "malformed arguments"}
        return await inner(value)

    descriptor = descriptor.model_copy(
        update={"input_schema": {"value": "str"}},
    )
    env.registry.rebind(descriptor, _str_tolerant)
    return True

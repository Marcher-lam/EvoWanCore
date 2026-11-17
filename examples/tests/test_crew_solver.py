"""V14-T2 crew solver：单票据 solve——无技能走弯路、有技能直通（Fake 剧本）。"""

from pathlib import Path

from examples.crew.domain import Invoice, dirty_payload


async def test_solve_without_skill_fails_with_detour(tmp_path: Path) -> None:
    """无归一化技能：Fake 模型交脏值→grader 拒→重试仍错（弯路失败，步数≥3）。"""
    from examples.crew.solver import make_solver_env, solve_invoice

    inv = Invoice(
        invoice_id="i1", vendor="云启", amount=100.0,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )
    payload = dirty_payload(inv, "date_format")
    env = make_solver_env(tmp_path)
    result = await solve_invoice(env, inv, payload)
    assert not result.passed
    assert result.error_tags == ["date_format"]
    assert result.steps >= 3, f"无技能应走弯路（≥3 步），实际 {result.steps}"


async def test_solve_with_skill_straight_through(tmp_path: Path) -> None:
    """注册 normalize_date 技能后：首步调技能→交答案（直通，步数==2）。"""
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.solver import make_solver_env, solve_invoice

    env = make_solver_env(tmp_path)
    registered = await register_skill_for_mode(
        env, "date_format", workspace=tmp_path / "ws",
    )
    assert registered, "date_format 技能应注册成功"

    inv = Invoice(
        invoice_id="i2", vendor="云启", amount=100.0,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )
    payload = dirty_payload(inv, "date_format")
    result = await solve_invoice(env, inv, payload)
    assert result.passed, f"有技能应直通: {result.error_tags}"
    assert result.steps == 2, f"直通 = 技能一步 + 交答案一步，实际 {result.steps}"


async def test_solve_clean_invoice_always_fast(tmp_path: Path) -> None:
    """干净票据：无技能也直通（弯路只由脏模式触发）。"""
    from examples.crew.solver import make_solver_env, solve_invoice

    inv = Invoice(
        invoice_id="i3", vendor="云启", amount=100.0,
        date="2026-10-18", tax_rate=0.06, category="hardware",
    )
    env = make_solver_env(tmp_path)
    result = await solve_invoice(env, inv, inv.model_dump())
    assert result.passed and result.steps == 1


async def test_evolve_gate_rejects_broken_candidate(tmp_path: Path) -> None:
    """T3 负例：合同不符的候选被门禁拒（绝不带病注册）。

    脚本经参数注入（不改模块全局——reload 还原是脆弱模式，终评审 M）。
    """
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.solver import make_solver_env

    env = make_solver_env(tmp_path)
    ok = await register_skill_for_mode(
        env, "date_format", workspace=tmp_path / "ws",
        scripts={"main.py": (
            "import json,sys\nprint(json.dumps({'normalized': 'WRONG'}))\n"
        )},
    )
    assert ok is False, "坏候选必须被合同门禁拒"
    ids = {d.id for d in env.registry.discover({})}
    assert "normalize_date" not in ids, "被拒候选不得进能力目录"


async def test_evolve_gate_rejects_single_sample_cheater(tmp_path: Path) -> None:
    """终评审 H2 破坏实验固化：只对单一日期正确的'作弊'技能被多样本合同拒。"""
    from examples.crew.evolve import register_skill_for_mode
    from examples.crew.solver import make_solver_env

    env = make_solver_env(tmp_path)
    ok = await register_skill_for_mode(
        env, "date_format", workspace=tmp_path / "ws",
        scripts={"main.py": (
            "import json, sys\n"
            "args = json.loads(sys.argv[1])\n"
            "# 只对合同样本 1 正确，其余 BROKEN（单样本合同会放行的作弊者）\n"
            "v = '2026-10-18' if args.get('value') == '2026/10/18' else 'BROKEN'\n"
            "print(json.dumps({'normalized': v}))\n"
        )},
    )
    assert ok is False, "多样本合同必须拒绝单点作弊技能"


async def test_v16_realmodel_fixes_regression() -> None:
    """V16 两个真模型缺陷修复的回归锁定（终评审 M6）。"""
    # 1) UserRoleGuard：system-only → 插入 user（qwen3.8 模板 400 修复）
    from agent_core.models import InferRequest, InferResponse, Modality, ModelCapabilities
    from examples.crew.domain import Invoice
    from examples.crew.solver import make_solver_env, solve_invoice
    from examples.crew.vlm_compat import UserRoleGuard

    class _Recording:
        def __init__(self):
            self.seen_messages = None

        def capabilities(self):
            return ModelCapabilities(model_id="m", inputs={Modality.TEXT})

        async def infer(self, request):
            self.seen_messages = request.messages
            return InferResponse(text="x")

    rec = _Recording()
    guard = UserRoleGuard(rec)
    await guard.infer(InferRequest(
        model_id="m", messages=[{"role": "system", "content": "sys"}],
    ))
    roles = [m["role"] for m in rec.seen_messages]
    assert "user" in roles, "system-only 必须被注入 user 角色"
    # 已有 user 时不重复注入
    await guard.infer(InferRequest(
        model_id="m",
        messages=[{"role": "system", "content": "s"},
                  {"role": "user", "content": "u"}],
    ))
    assert [m["role"] for m in rec.seen_messages].count("user") == 1

    # 2) submit 的 str-fields 容错（27b JSON 字符串怪癖）
    import tempfile
    from pathlib import Path as _P

    tmp = _P(tempfile.mkdtemp())
    env = make_solver_env(tmp)
    inv = Invoice(invoice_id="s1", vendor="v", amount=100.0,
                  date="2026-10-18", tax_rate=0.06, category="hardware")
    result = await solve_invoice(env, inv, inv.model_dump())
    assert result.passed  # 正常路径不受容错层影响


async def test_tool_first_goal_visible_to_model() -> None:
    """V17 工具优先引导：goal 指令进 system prompt（真模型可见面）。"""
    import tempfile
    from pathlib import Path as P

    from examples.crew.crew import make_crew
    from examples.crew.learning import CrewMemory
    from examples.crew.solver import make_solver_env

    captured: dict = {}

    class _CapturingVLM:
        """捕获 ModelLoopPolicy 构造的 messages（不改行为）。"""

        def capabilities(self):
            from agent_core.models import Modality, ModelCapabilities

            return ModelCapabilities(
                model_id="cap", inputs={Modality.TEXT, Modality.IMAGE},
            )

        async def infer(self, request):
            from agent_core.models import InferResponse

            captured["messages"] = request.messages
            captured["tools"] = request.tools
            return InferResponse(text="done")

    tmp = P(tempfile.mkdtemp())
    env = make_solver_env(tmp, vlm=_CapturingVLM(), max_steps=1)
    mem = CrewMemory(tmp / "m.db")
    crew = make_crew(env, workspace=tmp / "ws", memory=mem, tool_first=True)

    from examples.crew.domain import Invoice

    inv = Invoice(invoice_id="tf", vendor="v", amount=1.0,
                  date="2026-10-18", tax_rate=0.06, category="h")
    try:
        await crew._solve_one(inv, inv.model_dump())
    except Exception:  # max_steps=1 预期失败——只验证 prompt
        pass
    system_text = " ".join(
        str(m.get("content", "")) for m in captured.get("messages", [])
        if m.get("role") == "system"
    )
    assert "工具优先" in system_text or "TOOL-FIRST" in system_text, \
        f"tool_first 指令必须进 system prompt: {system_text[:120]}"

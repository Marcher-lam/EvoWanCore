"""examples 测试共享装置。"""

from pathlib import Path

# 平台包路径（零侵入红线扫描用——新增平台包记得同步此处）
PLATFORM_PATHS = [
    "agent-core/src", "agent-sdk/src", "agent-adapters/src",
    "agent-explore/src", "agent-evolve/src", "agent-devtools/src",
    "agent-sdk-ts/src",
]


def assert_platform_clean(repo_root: Path, concept: str) -> None:
    """红线：平台包源码零应用概念（cwd 锚定 + rc 语义，不可空过）。"""
    import subprocess

    result = subprocess.run(
        ["git", "grep", "-il", concept, "--", *PLATFORM_PATHS],
        capture_output=True, text=True, cwd=repo_root,
    )
    assert result.returncode != 0 or result.stdout.strip() == "", (
        f"平台包出现 {concept} 概念: {result.stdout.strip()}"
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"git grep 异常 rc={result.returncode}: {result.stderr}"
        )


def mixed_batch(modes: list[str | None]) -> list:
    """混合票据批（脏模式序列 → [(Invoice, payload)]）。"""
    from examples.crew.domain import Invoice, dirty_payload

    out = []
    for i, mode in enumerate(modes):
        inv = Invoice(
            invoice_id=f"b-{i}", vendor=f"商家{i}",
            amount=100.0 * (i + 1), date="2026-10-18",
            tax_rate=0.06, category="hardware",
        )
        out.append((inv, dirty_payload(inv, mode) if mode else inv.model_dump()))
    return out

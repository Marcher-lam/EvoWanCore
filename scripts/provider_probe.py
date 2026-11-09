"""Kickoff 供应商探测入口：python scripts/provider_probe.py"""

import asyncio
import sys
from pathlib import Path

from agent_adapters.provider_probe import load_config_from_env, probe_providers, write_report


def main() -> int:
    config = load_config_from_env()
    report = asyncio.run(probe_providers(config))
    out = Path("reports/provider_probe.json")
    write_report(report, out)
    for name in ("chat", "vlm", "realtime"):
        r = getattr(report, name)
        mark = "✓" if r.available else "✗"
        print(f"{mark} {name}: {r.detail}")
    print(f"report → {out}")
    return 0 if all(getattr(report, n).available for n in ("chat", "vlm", "realtime")) else 1


if __name__ == "__main__":
    sys.exit(main())

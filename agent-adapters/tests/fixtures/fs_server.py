"""T25：最小 MCP filesystem server（官方 Python SDK，stdio）。

用法：python fs_server.py <root> —— 工具限定在 root 内读写：
  list_dir(path) -> 目录清单；read_text(path) -> 文件文本内容。
路径逃逸（.. / 绝对路径）拒绝。参考验收 fixture（npm 不可用，Python 自建）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

server = MCPServer(name="fs-fixture")


def _resolve(root: Path, path: str) -> Path:
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise ToolError(f"路径逃逸（仅允许 root 内）: {path}")
    return candidate


def make_server(root: str) -> MCPServer:
    root_path = Path(root).resolve()

    def list_dir(path: str = ".") -> str:
        """列出目录内条目（名字/类型，逗号分隔）。"""
        target = _resolve(root_path, path)
        if not target.is_dir():
            raise ToolError(f"不是目录: {path}")
        entries = sorted(
            f"{p.name}{'/' if p.is_dir() else ''}" for p in target.iterdir()
        )
        return ", ".join(entries) if entries else "(empty)"

    def read_text(path: str) -> str:
        """读取文本文件内容。"""
        target = _resolve(root_path, path)
        if not target.is_file():
            raise ToolError(f"不是文件: {path}")
        return target.read_text(encoding="utf-8")

    server.add_tool(list_dir, name="list_dir", description="列出 root 内目录")
    server.add_tool(read_text, name="read_text", description="读取 root 内文本文件")
    return server


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python fs_server.py <root>")
    make_server(sys.argv[1]).run(transport="stdio")


if __name__ == "__main__":
    main()

"""MCP stdio server exposing the standalone read-only bb-browser adapter."""

from __future__ import annotations

from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations
except ModuleNotFoundError as exc:  # pragma: no cover - installation guidance
    raise SystemExit(
        'MCP dependency is missing. Install requirements.txt in this server virtual environment.'
    ) from exc

from backend import (
    browser_status as _browser_status,
    x_search as _x_search,
    xiaohongshu_comments as _xiaohongshu_comments,
    xiaohongshu_note as _xiaohongshu_note,
    xiaohongshu_search as _xiaohongshu_search,
)


server = MCPServer(
    name="bb-browser",
    description="Read-only X and Xiaohongshu research through bb-browser and a real Chrome profile.",
    instructions=(
        "All tools are read-only. The user must manually log in to X and Xiaohongshu "
        "inside the selected Chrome profile. Never claim login or retrieval succeeded when "
        "a tool returns LOGIN_REQUIRED or BROWSER_NOT_READY."
    ),
    version="0.1.0",
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


@server.tool(annotations=READ_ONLY, structured_output=True)
def bb_browser_status() -> dict[str, Any]:
    """Check bb-browser, CDP, managed profile, and required adapter readiness."""
    return _browser_status()


@server.tool(annotations=READ_ONLY, structured_output=True)
def search_x(query: str, count: int = 20, result_type: str = "latest") -> dict[str, Any]:
    """Search X posts using the logged-in real Chrome profile (count 1-50)."""
    return _x_search(query=query, count=count, result_type=result_type)


@server.tool(annotations=READ_ONLY, structured_output=True)
def search_xiaohongshu(keyword: str, sort: str = "general", limit: int = 10) -> dict[str, Any]:
    """Search Xiaohongshu notes; sort is general/latest/likes/comments/collects."""
    return _xiaohongshu_search(keyword=keyword, sort=sort, limit=limit)


@server.tool(annotations=READ_ONLY, structured_output=True)
def read_xiaohongshu_note(note_id_or_url: str) -> dict[str, Any]:
    """Read one Xiaohongshu note by ID or the full search-result URL."""
    return _xiaohongshu_note(note_id_or_url)


@server.tool(annotations=READ_ONLY, structured_output=True)
def read_xiaohongshu_comments(note_id_or_url: str) -> dict[str, Any]:
    """Read comments for one Xiaohongshu note by ID or full URL."""
    return _xiaohongshu_comments(note_id_or_url)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

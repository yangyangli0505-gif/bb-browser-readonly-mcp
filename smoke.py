"""Exercise the MCP protocol without executing bb-browser or using a browser."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


BASE = Path(__file__).resolve().parent
EXPECTED_TOOLS = {
    "bb_browser_status",
    "search_x",
    "search_xiaohongshu",
    "read_xiaohongshu_note",
    "read_xiaohongshu_comments",
}


def decode(result: Any) -> dict[str, Any]:
    payload = result.model_dump(exclude_none=True)
    for key in ("structured_content", "structuredContent"):
        if isinstance(payload.get(key), dict):
            return payload[key]
    for item in payload.get("content", []):
        if item.get("type") == "text":
            try:
                value = json.loads(item["text"])
            except (ValueError, KeyError):
                continue
            if isinstance(value, dict):
                return value
    raise AssertionError("Tool did not return a JSON object")


async def smoke() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-E", "-s", str(BASE / "server.py")],
        cwd=str(BASE),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "bb-browser"
            available = await session.list_tools()
            assert {tool.name for tool in available.tools} == EXPECTED_TOOLS
            # The backend rejects this before invoking any external command.
            invalid = decode(await session.call_tool("search_xiaohongshu", {"keyword": ""}))
            assert invalid.get("ok") is False
            assert invalid.get("error_code") == "INVALID_ARGUMENT"
    print(json.dumps({
        "handshake": "OK",
        "tool_count": len(EXPECTED_TOOLS),
        "tools": sorted(EXPECTED_TOOLS),
        "empty_keyword": "INVALID_ARGUMENT",
        "browser_operations_performed": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    asyncio.run(asyncio.wait_for(smoke(), timeout=30))

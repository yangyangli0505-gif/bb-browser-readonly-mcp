"""Read-only bb-browser execution layer for X and Xiaohongshu.

This module exposes a small allowlist of site adapters to the MCP server.
It validates arguments, invokes the separately installed bb-browser CLI,
and normalizes its JSON results and errors.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen


SERVER_DIR = Path(__file__).resolve().parent
MANAGED_PROFILE_DIR = Path.home() / ".bb-browser" / "browser" / "user-data"
REQUIRED_ADAPTERS = {
    "twitter/search",
    "xiaohongshu/search",
    "xiaohongshu/note",
    "xiaohongshu/comments",
}

_LAST_SITE_CALL = 0.0



def _resolve_command() -> list[str]:
    """Run Node directly so search terms never pass through a Windows shell."""
    node = os.getenv("BB_BROWSER_NODE", "").strip() or shutil.which("node") or "node"
    default_cli = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "npm" / "node_modules" / "bb-browser" / "dist" / "cli.js"
    cli = Path(os.getenv("BB_BROWSER_CLI", "").strip() or default_cli)
    return [node, str(cli)]


def _parse_json(stdout: str) -> dict[str, Any] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"result": value}
    except json.JSONDecodeError:
        # bb-browser can occasionally print a log line before the JSON body.
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            return None
        try:
            value = json.loads(text[start:])
            return value if isinstance(value, dict) else {"result": value}
        except json.JSONDecodeError:
            return None


def _error_code(message: str, hint: str = "") -> tuple[str, bool]:
    combined = f"{message} {hint}".lower()
    if any(token in combined for token in ("log in", "logged in", "ct0 cookie", "登录", "未登录")):
        return "LOGIN_REQUIRED", True
    if "adapter" in combined and "not found" in combined:
        return "ADAPTER_NOT_INSTALLED", False
    if any(token in combined for token in (
        "no page target", "not connected", "daemon", "did not start",
        "cannot find a chromium", "browser",
    )):
        return "BROWSER_UNAVAILABLE", False
    if "timeout" in combined:
        return "TIMEOUT", False
    return "BB_BROWSER_ERROR", False


def _run_bb(arguments: list[str], *, timeout_s: float = 90.0) -> dict[str, Any]:
    command = _resolve_command()
    env = dict(os.environ)
    try:
        completed = subprocess.run(
            [*command, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
            cwd=SERVER_DIR,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error_code": "BB_BROWSER_NOT_INSTALLED",
            "message": f"Cannot find bb-browser command: {command}",
            "hint": "Install it once with: npm install -g bb-browser",
            "requires_manual_login": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_code": "TIMEOUT",
            "message": f"bb-browser exceeded the {timeout_s:g}s timeout",
            "hint": "Check the managed Chrome window and retry after any login or verification step.",
            "requires_manual_login": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "BB_BROWSER_EXEC_FAILED",
            "message": f"{type(exc).__name__}: {exc}",
            "hint": "Check BB_BROWSER_NODE, BB_BROWSER_CLI and BB_BROWSER_CDP_URL.",
            "requires_manual_login": False,
        }

    payload = _parse_json(completed.stdout)
    if payload is None:
        detail = (completed.stderr or completed.stdout or "").strip()[:500]
        return {
            "ok": False,
            "error_code": "INVALID_BB_BROWSER_OUTPUT",
            "message": detail or f"bb-browser exited with code {completed.returncode} without JSON output",
            "hint": "Run the same bb-browser command in a terminal to inspect its browser state.",
            "requires_manual_login": False,
        }

    if payload.get("success") is False and "error" not in payload:
        payload["error"] = payload.get("message") or "bb-browser command failed"

    if "error" in payload:
        error = payload.get("error")
        message = str(error.get("message") if isinstance(error, dict) else error)
        hint = str(payload.get("hint") or "")
        code, login = _error_code(message, hint)
        return {
            "ok": False,
            "error_code": code,
            "message": message,
            "hint": hint,
            "requires_manual_login": login,
        }

    data = payload.get("result") if "result" in payload else payload
    return {
        "ok": completed.returncode == 0,
        "error_code": None if completed.returncode == 0 else "BB_BROWSER_EXIT_NONZERO",
        "data": data,
        "requires_manual_login": False,
    }


def _reachable_cdp(cdp_url: str) -> bool:
    if not cdp_url:
        return False
    try:
        parsed = urlparse(cdp_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or not parsed.port:
            return False
        with urlopen(cdp_url.rstrip("/") + "/json/version", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def _site_call(adapter: str, arguments: list[str], *, timeout_s: float = 90.0) -> dict[str, Any]:
    if adapter not in REQUIRED_ADAPTERS:
        return {
            "ok": False,
            "error_code": "ADAPTER_NOT_ALLOWED",
            "message": f"Adapter is not in the read-only allowlist: {adapter}",
            "hint": f"Allowed adapters: {', '.join(sorted(REQUIRED_ADAPTERS))}",
            "requires_manual_login": False,
        }

    global _LAST_SITE_CALL
    minimum_interval = max(0.0, float(os.getenv("BB_BROWSER_MIN_INTERVAL_S", "0.8")))
    wait_s = minimum_interval - (time.monotonic() - _LAST_SITE_CALL)
    if wait_s > 0:
        time.sleep(wait_s)
    result = _run_bb(["site", adapter, *arguments, "--json"], timeout_s=timeout_s)
    _LAST_SITE_CALL = time.monotonic()
    return result


def browser_status() -> dict[str, Any]:
    """Return execution readiness without reading or exposing cookies."""
    command = _resolve_command()
    installed = bool(shutil.which(command[0]) or Path(command[0]).is_file()) and Path(command[1]).is_file()
    requested_cdp = os.getenv("BB_BROWSER_CDP_URL", "").strip()

    # --version is plain text in bb-browser 0.14, so use a direct safe probe.
    version_text = ""
    if installed:
        try:
            probe = subprocess.run(
                [*command, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10.0,
            )
            version_text = probe.stdout.strip()
        except Exception:
            pass

    status = _run_bb(["status", "--json"], timeout_s=15.0) if installed else {
        "ok": False,
        "error_code": "BB_BROWSER_NOT_INSTALLED",
    }
    site_list = _run_bb(["site", "list", "--json"], timeout_s=20.0) if installed else {
        "ok": False,
        "data": [],
    }
    adapters = site_list.get("data") if isinstance(site_list.get("data"), list) else []
    names = {str(item.get("name")) for item in adapters if isinstance(item, dict)}
    missing = sorted(REQUIRED_ADAPTERS - names)

    status_data = status.get("data") if isinstance(status.get("data"), dict) else {}
    effective_port = status_data.get("cdpPort") or 9222
    effective_cdp = requested_cdp or f"http://127.0.0.1:{effective_port}"
    cdp_connected = bool(status_data.get("cdpConnected")) or _reachable_cdp(effective_cdp)
    warnings: list[str] = []
    if requested_cdp and not _reachable_cdp(requested_cdp):
        warnings.append(
            f"Configured CDP is unreachable: {requested_cdp}. bb-browser may fall back to its managed profile."
        )
    if missing:
        warnings.append("Missing adapters; run: bb-browser site update")

    ready = installed and not missing and cdp_connected
    return {
        "ok": ready,
        "error_code": None if ready else "BROWSER_NOT_READY",
        "data": {
            "command": command,
            "version": version_text,
            "browser_running": bool(status_data.get("running")) or cdp_connected,
            "cdp_connected": cdp_connected,
            "effective_cdp": effective_cdp,
            "configured_cdp": requested_cdp,
            "configured_cdp_reachable": _reachable_cdp(requested_cdp),
            "managed_profile": str(MANAGED_PROFILE_DIR),
            "required_adapters": sorted(REQUIRED_ADAPTERS),
            "missing_adapters": missing,
            "warnings": warnings,
            "login": {
                "x": "Manual login required once in the selected Chrome profile.",
                "xiaohongshu": "Manual QR/password login required once in the selected Chrome profile.",
            },
        },
        "requires_manual_login": False,
    }


def x_search(query: str, count: int = 20, result_type: str = "latest") -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "error_code": "INVALID_ARGUMENT", "message": "query is required"}
    count = max(1, min(int(count), 50))
    result_type = result_type if result_type in {"latest", "top"} else "latest"
    return _site_call(
        "twitter/search",
        [query, "--count", str(count), "--type", result_type],
        timeout_s=90.0,
    )


def xiaohongshu_search(keyword: str, sort: str = "general", limit: int = 10) -> dict[str, Any]:
    keyword = str(keyword or "").strip()
    if not keyword:
        return {"ok": False, "error_code": "INVALID_ARGUMENT", "message": "keyword is required"}
    sort = sort if sort in {"general", "latest", "likes", "comments", "collects"} else "general"
    limit = max(1, min(int(limit), 20))
    result = _site_call("xiaohongshu/search", [keyword, "--sort", sort], timeout_s=90.0)
    if result.get("ok") and isinstance(result.get("data"), dict):
        notes = result["data"].get("notes")
        if isinstance(notes, list):
            result["data"]["notes"] = notes[:limit]
            result["data"]["count"] = len(result["data"]["notes"])
    return result


def xiaohongshu_note(note_id_or_url: str) -> dict[str, Any]:
    value = str(note_id_or_url or "").strip()
    if not value:
        return {"ok": False, "error_code": "INVALID_ARGUMENT", "message": "note_id_or_url is required"}
    return _site_call("xiaohongshu/note", [value], timeout_s=90.0)


def xiaohongshu_comments(note_id_or_url: str) -> dict[str, Any]:
    value = str(note_id_or_url or "").strip()
    if not value:
        return {"ok": False, "error_code": "INVALID_ARGUMENT", "message": "note_id_or_url is required"}
    return _site_call("xiaohongshu/comments", [value], timeout_s=90.0)


def open_login_page(site: str) -> dict[str, Any]:
    """Open a visible login page. This is intentionally not exposed via MCP."""
    urls = {
        "x": "https://x.com",
        "xiaohongshu": "https://www.xiaohongshu.com",
    }
    site = str(site or "").strip().lower()
    if site not in urls:
        return {
            "ok": False,
            "error_code": "INVALID_ARGUMENT",
            "message": "site must be x or xiaohongshu",
        }
    return _run_bb(["open", urls[site], "--json"], timeout_s=30.0)


__all__ = [
    "REQUIRED_ADAPTERS",
    "browser_status",
    "open_login_page",
    "x_search",
    "xiaohongshu_comments",
    "xiaohongshu_note",
    "xiaohongshu_search",
]

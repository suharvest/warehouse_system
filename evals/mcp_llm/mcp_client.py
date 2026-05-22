"""MCP stdio client wrapper — spawn mcp/warehouse_mcp.py and expose
list_tools + call_tool helpers; convert tools to OpenAI function schema."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_stdio_params(
    mcp_script: Path,
    backend_url: str,
    api_key: str,
    extra_env: dict | None = None,
) -> StdioServerParameters:
    env = {
        **os.environ,
        "WAREHOUSE_API_URL": backend_url.rstrip("/") + "/api",
        "WAREHOUSE_API_KEY": api_key,
        "MCP_DEBUG": "0",
        "PYTHONUNBUFFERED": "1",
    }
    if extra_env:
        env.update(extra_env)
    return StdioServerParameters(
        command="uv",
        args=["run", "--extra", "eval", "python", str(mcp_script)],
        env=env,
        cwd=str(PROJECT_ROOT),
    )


@asynccontextmanager
async def mcp_session(params: StdioServerParameters):
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as sess:
            await sess.initialize()
            yield sess


# DashScope (and possibly other vendors) treat some tool names as reserved built-ins
# (e.g. `search` triggers `Tool names are not allowed to be [search]` on qwen3-1.7b/4b/14b).
# Apply a static rename here; the reverse map is used in run.py before call_tool.
TOOL_NAME_ALIAS = {
    "search": "warehouse_search",
}
TOOL_NAME_ALIAS_REVERSE = {v: k for k, v in TOOL_NAME_ALIAS.items()}


def mcp_tools_to_openai(tools_result: Any) -> list[dict]:
    """Convert mcp.list_tools() result to OpenAI function tool schemas."""
    out = []
    for t in tools_result.tools:
        params = t.inputSchema or {"type": "object", "properties": {}}
        # OpenAI expects a top-level type=object with properties
        if "type" not in params:
            params["type"] = "object"
        out.append(
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME_ALIAS.get(t.name, t.name),
                    "description": (t.description or "")[:1024],
                    "parameters": params,
                },
            }
        )
    return out


def build_tool_schema_map(tools_openai: list[dict]) -> dict[str, set[str]]:
    """Map real tool name (post-reverse) -> set of allowed parameter keys.
    Used to drop LLM-hallucinated extra kwargs before call_tool, to prevent
    server-side pydantic ValidationError that may kill the stdio session."""
    out: dict[str, set[str]] = {}
    for t in tools_openai:
        fn = t.get("function", {})
        name = fn.get("name", "")
        real = TOOL_NAME_ALIAS_REVERSE.get(name, name)
        props = (fn.get("parameters") or {}).get("properties") or {}
        out[real] = set(props.keys())
    return out


def sanitize_tool_args(real_name: str, args: dict, schema_map: dict[str, set[str]]) -> tuple[dict, list[str]]:
    """Drop keys not in the tool's schema. Returns (clean_args, dropped_keys)."""
    if not args:
        return {}, []
    allowed = schema_map.get(real_name)
    if allowed is None:
        return dict(args), []
    clean = {k: v for k, v in args.items() if k in allowed}
    dropped = [k for k in args.keys() if k not in allowed]
    return clean, dropped


def tool_result_to_json(result: Any) -> dict | str:
    """Pull a JSON dict out of MCP CallToolResult (FastMCP wraps return as text content)."""
    contents = getattr(result, "content", None) or []
    pieces = []
    for c in contents:
        text = getattr(c, "text", None)
        if text is not None:
            pieces.append(text)
    joined = "".join(pieces).strip()
    if not joined:
        return {"_raw": "", "isError": bool(getattr(result, "isError", False))}
    try:
        parsed = json.loads(joined)
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}
    except json.JSONDecodeError:
        return {"_text": joined}

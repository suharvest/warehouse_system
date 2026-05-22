"""OpenAI-compatible streaming client with TTFT capture.

Skip role-only chunk per spec §6.7: first content/tool-call delta with payload
marks t_first_token.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI


@dataclass
class ToolCallAcc:
    id: str = ""
    name: str = ""
    arguments: str = ""  # JSON string, possibly partial across deltas


@dataclass
class LLMTurnResult:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{id,name,arguments(dict)}]
    finish_reason: str | None = None
    ttft: float | None = None
    e2e: float = 0.0
    usage: dict = field(default_factory=dict)
    error: str | None = None


def _has_payload_delta(delta) -> bool:
    """Return True if delta carries actual content or tool-call payload (not role-only)."""
    c = getattr(delta, "content", None)
    if c:
        return True
    tcs = getattr(delta, "tool_calls", None) or []
    for tc in tcs:
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        if getattr(fn, "name", None) or getattr(fn, "arguments", None):
            return True
        if getattr(tc, "id", None):
            return True
    return False


def call_llm_stream(
    client: OpenAI,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float = 0.3,
    tool_choice: str | dict = "auto",
    timeout: float = 60.0,
    extra_body: dict | None = None,
    seed: int | None = None,
) -> LLMTurnResult:
    res = LLMTurnResult()
    t0 = time.time()
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    if extra_body:
        kwargs["extra_body"] = extra_body
    if seed is not None:
        kwargs["seed"] = seed
    try:
        stream = client.chat.completions.create(timeout=timeout, **kwargs)
    except Exception as e:
        res.error = f"llm_api_error: {e}"
        res.e2e = time.time() - t0
        return res

    acc_tools: dict[int, ToolCallAcc] = {}
    content_buf: list[str] = []
    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if res.ttft is None and _has_payload_delta(delta):
                res.ttft = time.time() - t0
            if getattr(delta, "content", None):
                content_buf.append(delta.content)
            for tc in getattr(delta, "tool_calls", None) or []:
                idx = tc.index if tc.index is not None else 0
                slot = acc_tools.setdefault(idx, ToolCallAcc())
                if getattr(tc, "id", None):
                    slot.id = tc.id
                fn = getattr(tc, "function", None)
                if fn:
                    if getattr(fn, "name", None):
                        slot.name = fn.name
                    if getattr(fn, "arguments", None):
                        slot.arguments += fn.arguments
            if choice.finish_reason:
                res.finish_reason = choice.finish_reason
    except Exception as e:
        res.error = f"stream_error: {e}"

    res.content = "".join(content_buf)
    res.e2e = time.time() - t0
    for idx in sorted(acc_tools.keys()):
        slot = acc_tools[idx]
        try:
            args = json.loads(slot.arguments) if slot.arguments else {}
        except json.JSONDecodeError:
            args = {"_raw_arguments": slot.arguments, "_parse_error": True}
        res.tool_calls.append(
            {"id": slot.id or f"call_{idx}", "name": slot.name, "arguments": args}
        )
    return res

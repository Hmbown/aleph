"""Codex MCP-backed sub-query runner."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "compose_sub_query_prompt",
    "build_codex_mcp_tool_call",
    "extract_codex_mcp_result_text",
    "run_codex_mcp_sub_query",
    "suppress_mcp_notification_validation_logs",
]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_plain_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    return None


def compose_sub_query_prompt(prompt: str, context_slice: str | None = None) -> str:
    """Anchor the sub-agent with the caller-provided slice when available.

    Shared-session MCP access is additive, not a replacement for the slice the
    parent already selected. Keeping the slice in the prompt gives the sub-agent
    a fast starting point and avoids unnecessary re-exploration of the same
    context through tools.
    """

    if not context_slice:
        return prompt
    return f"{prompt}\n\n---\nContext:\n{context_slice}"


def extract_codex_mcp_result_text(result: Any) -> tuple[str | None, str | None]:
    result_dict = _to_plain_dict(result)
    if result_dict is not None and "structuredContent" in result_dict:
        structured = _to_plain_dict(result_dict.get("structuredContent"))
    else:
        structured = _to_plain_dict(getattr(result, "structuredContent", None))
    thread_id = None
    if structured is not None:
        thread_id = _text(structured.get("threadId") or structured.get("conversationId"))
        content = structured.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip(), thread_id

    parts: list[str] = []
    raw_items = None
    if result_dict is not None and isinstance(result_dict.get("content"), list):
        raw_items = result_dict.get("content")
    else:
        raw_items = getattr(result, "content", []) or []

    for item in raw_items:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
            continue
        item_dict = _to_plain_dict(item)
        if item_dict is not None:
            maybe_text = item_dict.get("text")
            if isinstance(maybe_text, str) and maybe_text.strip():
                parts.append(maybe_text.strip())

    if parts:
        return "\n".join(parts), thread_id
    return None, thread_id


@contextmanager
def suppress_mcp_notification_validation_logs():
    original_warning = logging.warning

    def _filtered_warning(msg: object, *args: object, **kwargs: object) -> None:
        text = str(msg)
        if args:
            try:
                text = text % args
            except Exception:
                text = str(msg)
        if text.startswith("Failed to validate notification:"):
            return
        original_warning(msg, *args, **kwargs)

    logging.warning = _filtered_warning
    try:
        yield
    finally:
        logging.warning = original_warning


def build_codex_mcp_tool_call(
    prompt: str,
    cwd: Path | None = None,
    mcp_server_url: str | None = None,
    mcp_server_name: str = "aleph_shared",
    trust_mcp_server: bool = True,
    model: str | None = None,
    reasoning_effort: str | None = None,
    profile: str | None = None,
    thread_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build a Codex MCP tool name + arguments payload."""
    config: dict[str, Any] = {}
    if reasoning_effort:
        config["model_reasoning_effort"] = reasoning_effort
    if mcp_server_url:
        config["mcp_servers"] = {
            mcp_server_name: {
                "transport": "streamable_http",
                "url": mcp_server_url,
                "trust": trust_mcp_server,
            }
        }

    arguments: dict[str, Any] = {"prompt": prompt}
    tool_name = "codex"
    if thread_id:
        tool_name = "codex-reply"
        arguments["threadId"] = thread_id
    else:
        arguments["approval-policy"] = "never"
        arguments["sandbox"] = "workspace-write"
        if cwd is not None:
            arguments["cwd"] = str(cwd)
        if model:
            arguments["model"] = model
        if profile:
            arguments["profile"] = profile
        if config:
            arguments["config"] = config
    return tool_name, arguments


async def run_codex_mcp_sub_query(
    prompt: str,
    context_slice: str | None = None,
    *,
    timeout: float = 300.0,
    cwd: Path | None = None,
    max_output_chars: int = 50_000,
    max_context_chars: int = 20_000,
    mcp_server_url: str | None = None,
    mcp_server_name: str = "aleph_shared",
    trust_mcp_server: bool = True,
    model: str | None = None,
    reasoning_effort: str | None = None,
    profile: str | None = None,
    thread_id: str | None = None,
) -> tuple[bool, str, str | None]:
    """Run a Codex sub-query through `codex mcp-server`."""

    if context_slice and max_context_chars > 0 and len(context_slice) > max_context_chars:
        context_slice = context_slice[:max_context_chars]

    full_prompt = compose_sub_query_prompt(prompt, context_slice)

    tool_name, arguments = build_codex_mcp_tool_call(
        prompt=full_prompt,
        cwd=cwd,
        mcp_server_url=mcp_server_url,
        mcp_server_name=mcp_server_name,
        trust_mcp_server=trust_mcp_server,
        model=model,
        reasoning_effort=reasoning_effort,
        profile=profile,
        thread_id=thread_id,
    )

    try:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except Exception as e:
        return False, f"Codex MCP support is unavailable: {e}", None

    params = StdioServerParameters(
        command="codex",
        args=["mcp-server", "-c", "mcp_servers={}"],
        env=os.environ.copy(),
        cwd=str(cwd) if cwd is not None else None,
    )

    try:
        with suppress_mcp_notification_validation_logs():
            async with stdio_client(params) as streams:
                read_stream, write_stream = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        name=tool_name,
                        arguments=arguments,
                        read_timeout_seconds=timedelta(seconds=timeout),
                    )
    except FileNotFoundError:
        return False, "CLI backend 'codex' not found. Install it or use API fallback.", None
    except Exception as e:
        return False, f"Codex MCP error: {e}", None

    output, resolved_thread_id = extract_codex_mcp_result_text(result)
    if not output:
        payload = _to_plain_dict(result)
        if payload is not None:
            output = json.dumps(payload, ensure_ascii=True)
        else:
            output = str(result)

    if len(output) > max_output_chars:
        output = output[:max_output_chars] + "\n...[truncated]"

    return True, output, resolved_thread_id

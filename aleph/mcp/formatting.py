"""Formatting helpers for MCP tool responses."""

from __future__ import annotations

import json
from typing import Any, Callable, Literal, cast

from ..types import ContextMetadata, ExecutionResult
from .session import _coerce_context_to_text

DEFAULT_TOOL_RESPONSE_MAX_CHARS = 10_000
DEFAULT_TOOL_TRUNCATION_SUFFIX = "\n... [TRUNCATED]"


def _truncate_tool_text(
    text: str,
    *,
    max_chars: int = DEFAULT_TOOL_RESPONSE_MAX_CHARS,
    truncation_suffix: str = DEFAULT_TOOL_TRUNCATION_SUFFIX,
) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    if max_chars <= len(truncation_suffix):
        return truncation_suffix[:max_chars], True

    # Keep a compact prefix/suffix preview instead of a large contiguous head.
    # This avoids spilling big raw blocks into model context while preserving signal.
    preview_each_side = min(400, max(0, (max_chars - len(truncation_suffix)) // 2))
    if preview_each_side == 0:
        keep = max_chars - len(truncation_suffix)
        return text[:keep] + truncation_suffix, True
    return (
        text[:preview_each_side] + truncation_suffix + text[-preview_each_side:]
    ), True


def _format_payload(
    payload: dict[str, Any],
    output: Literal["json", "markdown", "object"],
    *,
    max_chars: int = DEFAULT_TOOL_RESPONSE_MAX_CHARS,
    truncation_suffix: str = DEFAULT_TOOL_TRUNCATION_SUFFIX,
    coerce_context_to_text: Callable[[Any], str] = _coerce_context_to_text,
) -> str | dict[str, Any]:
    def _truncate_inline(text: str, limit: int) -> str:
        return _truncate_tool_text(
            text,
            max_chars=limit,
            truncation_suffix=truncation_suffix,
        )[0]

    def _sanitize(value: Any, *, key: str | None = None) -> Any:
        if key == "ctx":
            text = coerce_context_to_text(value)
            return {
                "redacted": True,
                "reason": "context_field_blocked",
                "original_chars": len(text),
                "value_preview": _truncate_inline(text, min(200, max_chars)),
            }

        if isinstance(value, dict):
            return {str(k): _sanitize(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [_sanitize(v, key=key) for v in value]
        if isinstance(value, str):
            return _truncate_inline(value, max_chars)
        return value

    # "object" mode returns the raw payload untouched — callers that request it
    # expect the full, untruncated data (e.g. for programmatic consumption).
    if output == "object":
        return payload

    safe_payload = cast(dict[str, Any], _sanitize(payload))

    rendered = json.dumps(safe_payload, ensure_ascii=False, indent=2)
    if output == "json":
        return _truncate_inline(rendered, max_chars)

    fence_overhead = len("```json\n\n```")
    json_limit = max(0, max_chars - fence_overhead)
    rendered = _truncate_inline(rendered, json_limit)
    return "```json\n" + rendered + "\n```"


def _format_error(
    message: str,
    output: Literal["json", "markdown", "object"],
    *,
    max_chars: int = DEFAULT_TOOL_RESPONSE_MAX_CHARS,
    truncation_suffix: str = DEFAULT_TOOL_TRUNCATION_SUFFIX,
    coerce_context_to_text: Callable[[Any], str] = _coerce_context_to_text,
) -> str | dict[str, Any]:
    if output == "markdown":
        return f"Error: {message}"
    return _format_payload(
        {"error": message},
        output=output,
        max_chars=max_chars,
        truncation_suffix=truncation_suffix,
        coerce_context_to_text=coerce_context_to_text,
    )


def _format_context_loaded(
    context_id: str,
    meta: ContextMetadata,
    line_number_base: int,
    note: str | None = None,
) -> str:
    line_desc = "1-based" if line_number_base == 1 else "0-based"
    msg = (
        f"Context loaded '{context_id}': {meta.size_chars:,} chars, "
        f"{meta.size_lines:,} lines, ~{meta.size_tokens_estimate:,} tokens "
        f"(line numbers {line_desc})."
    )
    if note:
        msg += f"\nNote: {note}"
    return msg


def _format_execution_result(
    result: ExecutionResult,
    *,
    max_chars: int = DEFAULT_TOOL_RESPONSE_MAX_CHARS,
    truncation_suffix: str = DEFAULT_TOOL_TRUNCATION_SUFFIX,
) -> str:
    """Format sandboxed execution results for output."""
    if result.error:
        text, _ = _truncate_tool_text(
            f"## Execution Error\n\n{result.error}",
            max_chars=max_chars,
            truncation_suffix=truncation_suffix,
        )
        return text

    res = ["## Execution Result\n"]
    formatting_truncated = False
    if result.stdout:
        stdout_text, was_truncated = _truncate_tool_text(
            result.stdout,
            max_chars=max_chars,
            truncation_suffix=truncation_suffix,
        )
        formatting_truncated = formatting_truncated or was_truncated
        res.append(f"**Output:**\n```\n{stdout_text}\n```")
    if result.stderr:
        stderr_text, was_truncated = _truncate_tool_text(
            result.stderr,
            max_chars=max_chars,
            truncation_suffix=truncation_suffix,
        )
        formatting_truncated = formatting_truncated or was_truncated
        res.append(f"**Stderr:**\n```\n{stderr_text}\n```")
    if result.return_value is not None:
        rendered = repr(result.return_value)
        rendered, was_truncated = _truncate_tool_text(
            rendered,
            max_chars=max_chars,
            truncation_suffix=truncation_suffix,
        )
        formatting_truncated = formatting_truncated or was_truncated
        res.append(f"**Return Value:** `{rendered}`")
    if result.variables_updated:
        res.append(
            f"\n**Variables Updated:** {', '.join(f'`{v}`' for v in result.variables_updated)}"
        )

    if result.truncated or formatting_truncated:
        res.append("\n*Note: Output was truncated*")

    out = "\n".join(res)
    out, _ = _truncate_tool_text(
        out,
        max_chars=max_chars,
        truncation_suffix=truncation_suffix,
    )
    return out


def _limit_json_items(
    items: list[Any],
    *,
    max_chars: int = DEFAULT_TOOL_RESPONSE_MAX_CHARS,
    to_jsonable: Callable[[Any], Any] | None = None,
) -> tuple[list[Any], bool]:
    serializer = to_jsonable or _to_jsonable
    used = 2  # [] delimiters
    limited: list[Any] = []

    for raw in items:
        item = serializer(raw)
        try:
            encoded = json.dumps(item, ensure_ascii=False)
        except Exception:
            encoded = json.dumps(str(item), ensure_ascii=False)

        projected = used + len(encoded) + (1 if limited else 0)
        if projected > max_chars:
            return limited, True

        limited.append(item)
        used = projected

    return limited, False


def _format_variable_value(
    name: str,
    value: Any,
    *,
    max_chars: int = DEFAULT_TOOL_RESPONSE_MAX_CHARS,
    truncation_suffix: str = DEFAULT_TOOL_TRUNCATION_SUFFIX,
    to_jsonable: Callable[[Any], Any] | None = None,
) -> Any:
    serializer = to_jsonable or _to_jsonable

    if value is None or isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, str):
        text, truncated = _truncate_tool_text(
            value,
            max_chars=max_chars,
            truncation_suffix=truncation_suffix,
        )
        if not truncated:
            return value
        return {
            "name": name,
            "truncated": True,
            "original_chars": len(value),
            "value_preview": text,
        }

    jsonable = serializer(value)
    try:
        rendered = json.dumps(jsonable, ensure_ascii=False)
    except Exception:
        rendered = str(jsonable)
    text, truncated = _truncate_tool_text(
        rendered,
        max_chars=max_chars,
        truncation_suffix=truncation_suffix,
    )
    if not truncated:
        return jsonable
    return {
        "name": name,
        "truncated": True,
        "original_chars": len(rendered),
        "value_preview": text,
    }


def _to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of MCP/Pydantic objects into JSON-serializable data."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return _to_jsonable(vars(obj))
        except Exception:
            pass
    return str(obj)

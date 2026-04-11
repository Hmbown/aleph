"""Context/session MCP tool registrations for the local server."""

from __future__ import annotations

import asyncio
import difflib
import json
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

from ..compat import normalize_content_format
from ..types import ContentFormat
from .io_utils import _detect_format
from .session import load_memory_pack_payload
from .workspace import LineNumberBase, _validate_line_number_base
from .workspace_contexts import binding_summary

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from .local_server import AlephMCPServerLocal
else:
    Context = Any


def register_context_tools(
    owner: "AlephMCPServerLocal",
    *,
    format_error: Callable[[str, Literal["json", "markdown", "object"]], str | dict[str, Any]],
) -> None:
    _tool = owner._tool_decorator

    @_tool()
    async def load_context(
        content: str | None = None,
        context_id: str = "default",
        format: str = "auto",
        line_number_base: LineNumberBase = 1,
        context: str | None = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str:
        """Load context into an in-memory REPL session."""
        text = content if content is not None else context
        if text is None:
            return "Error: content is required"
        try:
            base = _validate_line_number_base(line_number_base)
        except ValueError as exc:
            return f"Error: {exc}"

        normalized_format = normalize_content_format(format, allow_auto=True)
        fmt = cast(
            ContentFormat,
            _detect_format(text) if normalized_format == "auto" else normalized_format,
        )
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)
        meta = owner._create_session(text, context_id, fmt, base)
        return owner._format_context_loaded(context_id, meta, base)

    @_tool()
    async def list_contexts(
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """List all active context sessions and their status."""
        items = []
        for cid, session in owner._sessions.items():
            summary = binding_summary(session.workspace_binding)
            items.append({
                "id": cid,
                "chars": session.meta.size_chars,
                "lines": session.meta.size_lines,
                "iterations": session.iterations,
                "evidence": len(session.evidence),
                "workspace_binding": session.workspace_binding,
                "workspace_binding_summary": summary,
            })

        if output == "object":
            return {"count": len(items), "items": items}
        if output == "json":
            return json.dumps({"count": len(items), "items": items}, indent=2)

        lines = [f"Found {len(items)} active context session(s):\n"]
        for item in items:
            binding_note = (
                f" [{item['workspace_binding_summary']}]"
                if item["workspace_binding_summary"]
                else ""
            )
            lines.append(
                f"- **{item['id']}**: {item['chars']:,} chars, "
                f"{item['lines']:,} lines, {item['iterations']} iterations{binding_note}"
            )
        return "\n".join(lines)

    @_tool()
    async def diff_contexts(
        a: str,
        b: str,
        context_lines: int = 3,
        max_lines: int = 400,
        output: Literal["markdown", "text"] = "markdown",
    ) -> str:
        """Compare two context sessions using unified diff."""
        if a not in owner._sessions:
            return f"Error: Context '{a}' not found."
        if b not in owner._sessions:
            return f"Error: Context '{b}' not found."

        lines_a = str(owner._sessions[a].repl.get_variable("ctx") or "").splitlines()
        lines_b = str(owner._sessions[b].repl.get_variable("ctx") or "").splitlines()

        diff = list(
            difflib.unified_diff(
                lines_a,
                lines_b,
                fromfile=f"context:{a}",
                tofile=f"context:{b}",
                n=context_lines,
                lineterm="",
            )
        )

        if not diff:
            return f"Contexts '{a}' and '{b}' are identical."

        if len(diff) > max_lines:
            diff = diff[:max_lines] + ["... (diff truncated)"]

        diff_text = "\n".join(diff)
        rendered = (
            f"### Diff: {a} vs {b}\n\n```diff\n{diff_text}\n```"
            if output == "markdown"
            else diff_text
        )
        text, _ = owner._truncate_tool_text(rendered)
        return text

    @_tool()
    async def save_session(
        path: str = "aleph_session.json",
        context_id: str | None = None,
        session_id: str = "default",
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """Save session state to a file (Memory Pack)."""
        err = owner._require_actions(confirm, requires_write=True)
        if err:
            return format_error(err, output=output)
        if owner.context_policy == "isolated" and not confirm:
            return format_error(
                "Isolated policy requires confirm=true for session export (prevents accidental context leaks).\n"
                "To proceed: save_session(path=..., confirm=true)\n"
                "To switch policy: configure(context_policy='trusted')",
                output=output,
            )

        payload, skipped = owner._build_memory_pack_payload()
        try:
            scoped_path = owner._scoped_path(path)
        except Exception as exc:
            return format_error(f"Invalid path: {exc}", output=output)

        try:
            scoped_path.parent.mkdir(parents=True, exist_ok=True)
            with open(scoped_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
        except Exception as exc:
            return format_error(f"Failed to save: {exc}", output=output)

        message = f"Session saved to {path}."
        if skipped:
            message += f" Warning: skipped {len(skipped)} sessions due to serialization errors."

        if output == "object":
            return {"status": "success", "path": str(scoped_path), "skipped": skipped}
        if output == "json":
            return json.dumps({"status": "success", "path": str(scoped_path), "skipped": skipped})
        return message

    @_tool()
    async def load_session(
        path: str,
        context_id: str | None = None,
        session_id: str | None = None,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """Load session state from a file (Memory Pack)."""
        err = owner._require_actions(confirm)
        if err:
            return format_error(err, output=output)
        if owner.context_policy == "isolated" and not confirm:
            return format_error(
                "Isolated policy requires confirm=true for session import (prevents unvetted context rehydration).\n"
                "To proceed: load_session(path=..., confirm=true)\n"
                "To switch policy: configure(context_policy='trusted')",
                output=output,
            )

        try:
            scoped_path = owner._scoped_path(path)
            with open(scoped_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            return format_error(f"Failed to load: {exc}", output=output)

        try:
            loaded, skipped = load_memory_pack_payload(
                payload,
                sessions=owner._sessions,
                sandbox_config=owner.sandbox_config,
                configure_session=owner._configure_session,
                loop=asyncio.get_running_loop(),
                close_node_repl=owner._close_node_repl,
            )
        except ValueError as exc:
            return format_error(str(exc), output=output)

        message = f"Loaded {len(loaded)} session(s) from {path}."
        if skipped:
            message += f" Skipped {len(skipped)} invalid session(s)."
        if output == "object":
            return {"status": "success", "loaded": loaded, "skipped": skipped}
        if output == "json":
            return json.dumps({"status": "success", "loaded": loaded, "skipped": skipped})
        return message

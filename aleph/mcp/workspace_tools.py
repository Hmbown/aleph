"""Workspace-oriented MCP tool registrations for the local server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from ..types import ContentFormat
from .formatting import _format_error, _format_payload
from .workspace import _scoped_path
from .workspace_contexts import (
    build_workspace_manifest,
    refresh_workspace_binding,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from .local_server import AlephMCPServerLocal
else:
    Context = Any


def register_workspace_tools(owner: "AlephMCPServerLocal") -> None:
    _tool = owner._tool_decorator

    @_tool()
    async def load_workspace_manifest(
        paths: list[str] | str | None = None,
        context_id: str = "workspace",
        max_files: int = 2000,
        include_hidden: bool = False,
        confirm: bool = False,
        output: Literal["markdown", "json", "object"] = "markdown",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Load a compact manifest of the current workspace for large codebase/project analysis."""
        err = owner._require_actions(confirm)
        if err:
            return _format_error(err, output=output)
        if max_files <= 0:
            return _format_error("max_files must be greater than 0.", output=output)

        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)

        if isinstance(paths, str):
            paths = [paths]

        resolved_roots = []
        for path in paths or [str(owner.action_config.workspace_root)]:
            try:
                resolved = _scoped_path(
                    owner.action_config.workspace_root,
                    path,
                    owner.action_config.workspace_mode,
                )
            except Exception as exc:
                return _format_error(str(exc), output=output)
            if not resolved.exists():
                return _format_error(f"Path not found: {path}", output=output)
            resolved_roots.append(resolved)

        text, binding, note = build_workspace_manifest(
            workspace_root=owner.action_config.workspace_root,
            roots=resolved_roots,
            max_files=max_files,
            include_hidden=include_hidden,
        )
        meta = owner._create_session(text, context_id, ContentFormat.TEXT, 1)
        session = owner._sessions[context_id]
        session.workspace_binding = binding
        owner._record_action(
            session,
            note="load_workspace_manifest",
            snippet=", ".join(str(root) for root in resolved_roots)[:200],
        )

        payload = {
            "status": "success",
            "context_id": context_id,
            "workspace_root": str(owner.action_config.workspace_root),
            "roots": [str(root) for root in resolved_roots],
            "file_count": int(binding.get("file_count") or 0),
            "truncated": bool(binding.get("truncated") or False),
            "binding": binding,
            "note": note,
            "size_chars": meta.size_chars,
            "size_lines": meta.size_lines,
        }
        if output == "object":
            return payload
        if output == "json":
            return _format_payload(payload, output="json")
        return owner._format_context_loaded(context_id, meta, 1, note=note)

    @_tool()
    async def refresh_context(
        context_id: str = "default",
        confirm: bool = False,
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Refresh a context from its bound workspace file or manifest."""
        err = owner._require_actions(confirm)
        if err:
            return _format_error(err, output=output)
        if context_id not in owner._sessions:
            return _format_error(f"No context loaded with ID '{context_id}'.", output=output)

        session = owner._sessions[context_id]
        if not session.workspace_binding:
            return _format_error(
                "This context is not bound to a refreshable workspace file or manifest.",
                output=output,
            )

        try:
            text, fmt, note, refreshed_binding = refresh_workspace_binding(
                session.workspace_binding,
                max_read_bytes=owner.action_config.max_read_bytes,
                timeout_seconds=owner.action_config.max_cmd_seconds,
            )
        except Exception as exc:
            return _format_error(f"Refresh failed: {exc}", output=output)

        meta = owner._replace_session_context(
            text,
            context_id,
            fmt,
            session.line_number_base,
            preserve_state=True,
        )
        refreshed_session = owner._sessions[context_id]
        refreshed_session.iterations += 1
        refreshed_session.workspace_binding = refreshed_binding
        owner._record_action(
            refreshed_session,
            note="refresh_context",
            snippet=str(refreshed_binding.get("display_path") or refreshed_binding.get("kind") or context_id),
        )

        payload = {
            "status": "success",
            "context_id": context_id,
            "binding": refreshed_binding,
            "size_chars": meta.size_chars,
            "size_lines": meta.size_lines,
            "note": note,
        }
        if output == "object":
            return payload
        if output == "json":
            return _format_payload(payload, output="json")
        return owner._format_context_loaded(
            context_id,
            meta,
            refreshed_session.line_number_base,
            note=note or "Context refreshed from workspace binding.",
        )

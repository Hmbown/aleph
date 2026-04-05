"""Action MCP tool registrations for the local server.

Extracted from local_server.py to keep the server class focused on
orchestration while action tools live in their own module.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

from ..compat import normalize_content_format
from ..types import ContentFormat
from .io_utils import _load_text_from_path
from .workspace import (
    DEFAULT_LINE_NUMBER_BASE,
    LineNumberBase,
    _scoped_path,
    _validate_line_number_base,
    _resolve_line_number_base,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from .local_server import AlephMCPServerLocal
else:
    Context = Any

# Re-exported for compatibility imports in local_server.py
__all__ = [
    "register_action_tools",
]


def register_action_tools(
    owner: "AlephMCPServerLocal",
    *,
    format_error: Callable[
        [str, Literal["json", "markdown", "object"]], str | dict[str, Any]
    ],
    format_payload: Callable[
        [dict[str, Any], Literal["json", "markdown", "object"]], str | dict[str, Any]
    ],
) -> None:
    _tool = owner._tool_decorator

    @_tool()
    async def run_command(
        cmd: str,
        cwd: str | None = None,
        timeout_seconds: float | None = None,
        shell: bool = False,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Run a shell command."""
        err = owner._require_actions(confirm, requires_command=True)
        if err:
            return format_error(err, output=output)
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)

        session = owner._get_or_create_session(context_id)
        session.iterations += 1

        workspace_root = owner.action_config.workspace_root
        cwd_path = (
            _scoped_path(workspace_root, cwd, owner.action_config.workspace_mode)
            if cwd
            else workspace_root
        )
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else owner.action_config.max_cmd_seconds
        )

        if shell:
            user_shell = os.environ.get("SHELL", "/bin/sh")
            argv = [user_shell, "-lc", cmd]
        else:
            argv = shlex.split(cmd)
            if not argv:
                return format_error("Empty command", output=output)

        payload = await owner._run_subprocess(
            argv=argv, cwd=cwd_path, timeout_seconds=timeout
        )
        session.repl._namespace["last_command_result"] = payload
        owner._record_action(
            session,
            note="run_command",
            snippet=(payload.get("stdout") or payload.get("stderr") or "")[:200],
        )
        return format_payload(payload, output=output)

    @_tool()
    async def rg_search(
        pattern: str,
        paths: list[str] | str | None = None,
        glob: str | None = None,
        max_results: int = 200,
        load_context_id: str | None = None,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Fast codebase search using ripgrep (rg) with fallback scanning."""
        err = owner._require_actions(confirm)
        if err:
            return format_error(err, output=output)
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)
        if not pattern:
            return format_error("pattern is required", output=output)
        if isinstance(paths, str):
            paths = [paths]

        session = owner._get_or_create_session(context_id)
        session.iterations += 1

        workspace_root = owner.action_config.workspace_root
        resolved_paths: list[Path] = []
        for p in paths or [str(workspace_root)]:
            try:
                resolved = _scoped_path(
                    workspace_root, p, owner.action_config.workspace_mode
                )
            except Exception as e:
                return format_error(str(e), output=output)
            resolved_paths.append(resolved)

        matches: list[dict[str, Any]] = []
        truncated = False
        used_rg = False
        payload: dict[str, Any] | None = None

        rg_bin = shutil.which("rg")
        if rg_bin:
            used_rg = True
            argv = [rg_bin, "--vimgrep", pattern]
            if glob:
                argv.extend(["-g", glob])
            if max_results > 0:
                argv.extend(["-m", str(max_results)])
            argv.extend(str(p) for p in resolved_paths)
            payload = await owner._run_subprocess(
                argv=argv,
                cwd=workspace_root,
                timeout_seconds=owner.action_config.max_cmd_seconds,
            )
            matches, truncated = owner._parse_rg_vimgrep(
                payload.get("stdout") or "", max_results
            )
        else:
            matches, truncated = owner._python_rg_search(
                pattern,
                resolved_paths,
                glob,
                max_results,
            )

        hits_text = "\n".join(
            f"{m['path']}:{m['line']}:{m['column']}:{m['text']}" for m in matches
        )
        if load_context_id:
            meta = owner._create_session(
                hits_text, load_context_id, ContentFormat.TEXT, DEFAULT_LINE_NUMBER_BASE
            )
            session.repl._namespace["last_rg_loaded_context"] = load_context_id
            load_note = f"Loaded {len(matches)} match(es) into '{load_context_id}'."
        else:
            meta = None
            load_note = None

        result_payload: dict[str, Any] = {
            "pattern": pattern,
            "paths": [str(p) for p in resolved_paths],
            "used_rg": used_rg,
            "match_count": len(matches),
            "truncated": truncated,
            "matches": matches,
        }
        if payload:
            result_payload["command"] = payload.get("argv")
            result_payload["timed_out"] = payload.get("timed_out", False)
            result_payload["stderr"] = payload.get("stderr", "")
        if load_context_id:
            result_payload["loaded_context_id"] = load_context_id
            result_payload["loaded_meta"] = {
                "size_chars": meta.size_chars if meta else 0,
                "size_lines": meta.size_lines if meta else 0,
            }
            if load_note:
                result_payload["note"] = load_note

        session.repl._namespace["last_rg_result"] = result_payload
        owner._record_action(
            session, note="rg_search", snippet=f"{pattern} ({len(matches)} matches)"
        )

        if output == "object":
            return result_payload
        if output == "json":
            return json.dumps(result_payload, ensure_ascii=False, indent=2)

        parts = [
            "## rg_search Results",
            f"Pattern: `{pattern}`",
            f"Matches: {len(matches)}" + (" (truncated)" if truncated else ""),
        ]
        if load_note:
            parts.append(load_note)
        if matches:
            parts.append("")
            parts.extend(
                [
                    f"- {m['path']}:{m['line']}:{m['column']}: {m['text']}"
                    for m in matches[:20]
                ]
            )
            if len(matches) > 20:
                parts.append(f"... {len(matches) - 20} more")
        return "\n".join(parts)

    @_tool()
    async def read_file(
        path: str,
        start_line: int = 1,
        limit: int = 200,
        include_raw: bool = False,
        line_number_base: int | None = None,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Read file content (raw)."""
        err = owner._require_actions(confirm)
        if err:
            return format_error(err, output=output)
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)

        base_override: LineNumberBase | None = None
        if line_number_base is not None:
            try:
                base_override = _validate_line_number_base(line_number_base)
            except ValueError as e:
                return format_error(str(e), output=output)

        session = owner._get_or_create_session(context_id, base_override)
        session.iterations += 1
        try:
            base = _resolve_line_number_base(session, line_number_base)
        except ValueError as e:
            return format_error(str(e), output=output)

        if base == 1 and start_line == 0:
            start_line = 1
        if start_line < base:
            return format_error(f"start_line must be >= {base}", output=output)

        try:
            p = _scoped_path(
                owner.action_config.workspace_root,
                path,
                owner.action_config.workspace_mode,
            )
        except Exception as e:
            return format_error(str(e), output=output)

        if not p.exists() or not p.is_file():
            return format_error(f"File not found: {path}", output=output)

        data = p.read_bytes()
        if len(data) > owner.action_config.max_read_bytes:
            return format_error(
                f"File too large to read (>{owner.action_config.max_read_bytes} bytes): {path}",
                output=output,
            )

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start_idx = max(0, start_line - base)
        end_idx = min(len(lines), start_idx + max(0, limit))
        slice_lines = lines[start_idx:end_idx]
        numbered = "\n".join(
            f"{i + start_idx + base:>6}\t{line}" for i, line in enumerate(slice_lines)
        )
        end_line = (
            (start_idx + len(slice_lines) - 1 + base) if slice_lines else start_line
        )

        payload: dict[str, Any] = {
            "path": str(p),
            "start_line": start_line,
            "end_line": end_line,
            "limit": limit,
            "total_lines": len(lines),
            "line_number_base": base,
            "content": numbered,
        }
        if include_raw:
            payload["content_raw"] = "\n".join(slice_lines)
        session.repl._namespace["last_read_file_result"] = payload
        owner._record_action(
            session, note="read_file", snippet=f"{path} ({start_line}-{end_line})"
        )
        return format_payload(payload, output=output)

    @_tool()
    async def load_file(
        path: str,
        context_id: str = "default",
        format: str = "auto",
        line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE,
        confirm: bool = False,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str:
        """Load a workspace file into a context session."""
        from .workspace_contexts import make_file_binding

        err = owner._require_actions(confirm)
        if err:
            return f"Error: {err}"
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)

        try:
            base = _validate_line_number_base(line_number_base)
        except ValueError as e:
            return f"Error: {e}"

        try:
            p = _scoped_path(
                owner.action_config.workspace_root,
                path,
                owner.action_config.workspace_mode,
            )
        except Exception as e:
            return f"Error: {e}"

        if not p.exists() or not p.is_file():
            return f"Error: File not found: {path}"

        try:
            text, detected_fmt, warning = _load_text_from_path(
                p,
                owner.action_config.max_read_bytes,
                owner.action_config.max_cmd_seconds,
            )
        except ValueError as e:
            return f"Error: {e}"
        try:
            normalized_format = normalize_content_format(format, allow_auto=True)
            fmt = cast(
                ContentFormat,
                detected_fmt if normalized_format == "auto" else normalized_format,
            )
        except Exception as e:
            return f"Error: {e}"
        meta = owner._create_session(text, context_id, fmt, base)
        session = owner._sessions[context_id]
        session.workspace_binding = make_file_binding(
            p, owner.action_config.workspace_root
        )
        owner._record_action(session, note="load_file", snippet=str(p))
        return owner._format_context_loaded(context_id, meta, base, note=warning)

    @_tool()
    async def write_file(
        path: str,
        content: str,
        mode: Literal["overwrite", "append"] = "overwrite",
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Write file content."""
        err = owner._require_actions(confirm, requires_write=True)
        if err:
            return format_error(err, output=output)
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)

        session = owner._get_or_create_session(context_id)
        session.iterations += 1

        try:
            p = _scoped_path(
                owner.action_config.workspace_root,
                path,
                owner.action_config.workspace_mode,
            )
        except Exception as e:
            return format_error(str(e), output=output)

        payload_bytes = content.encode("utf-8", errors="replace")
        if len(payload_bytes) > owner.action_config.max_write_bytes:
            return format_error(
                f"Content too large to write (>{owner.action_config.max_write_bytes} bytes)",
                output=output,
            )

        p.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "ab" if mode == "append" else "wb"
        with open(p, file_mode) as f:
            f.write(payload_bytes)

        payload: dict[str, Any] = {
            "path": str(p),
            "bytes_written": len(payload_bytes),
            "mode": mode,
        }
        session.repl._namespace["last_write_file_result"] = payload
        owner._record_action(
            session, note="write_file", snippet=f"{path} ({len(payload_bytes)} bytes)"
        )
        return format_payload(payload, output=output)

    @_tool()
    async def run_tests(
        runner: Literal["auto", "pytest"] = "auto",
        args: list[str] | None = None,
        cwd: str | None = None,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Run project tests."""
        err = owner._require_actions(confirm, requires_command=True)
        if err:
            return format_error(err, output=output)
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)

        session = owner._get_or_create_session(context_id)
        session.iterations += 1

        workspace_root = owner.action_config.workspace_root
        cwd_path = (
            _scoped_path(workspace_root, cwd, owner.action_config.workspace_mode)
            if cwd
            else workspace_root
        )

        # Heuristics for test runner
        import sys as _sys

        runner_bin: str = str(runner)
        if runner == "auto":
            runner_bin = "pytest"

        # Use sys.executable -m to ensure the correct interpreter in venvs
        argv: list[str] = [_sys.executable, "-m", runner_bin]
        if args:
            argv.extend(args)

        payload = await owner._run_subprocess(
            argv=argv, cwd=cwd_path, timeout_seconds=owner.action_config.max_cmd_seconds
        )
        owner._record_action(
            session,
            note=f"run_tests: {runner}",
            snippet=(payload.get("stdout") or payload.get("stderr") or "")[:200],
        )
        return format_payload(payload, output=output)

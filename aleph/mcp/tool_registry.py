"""Tool registration for Aleph MCP local server."""

from __future__ import annotations

import asyncio
import difflib
import inspect
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from ..repl.sandbox import REPLEnvironment
from ..types import ContentFormat, ContextMetadata
from ..sub_query import detect_backend
from ..sub_query.cli_backend import CLI_BACKENDS, run_cli_sub_query
from ..sub_query.api_backend import run_api_sub_query
from . import actions as _actions
from .actions import ActionDeps
from .env_utils import DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS, _get_env_bool, _get_env_int
from .formatting import _format_context_loaded, _format_error, _format_payload, _to_jsonable
from .io_utils import _detect_format, _load_text_from_path
from .remote import _RemoteServerHandle
from .session import (
    MEMORY_PACK_RELATIVE_PATH,
    _Evidence,
    _Session,
    _analyze_text_context,
    _coerce_context_to_text,
    _session_from_payload,
    _session_to_payload,
)
from .workspace import (
    DEFAULT_LINE_NUMBER_BASE,
    LineNumberBase,
    _scoped_path,
    _validate_line_number_base,
)

def register_tools(server: Any) -> None:

    """Register all MCP tools."""
    self = server

    NO_CONTEXT_ERROR = "Error: No context loaded with ID '{context_id}'. Use load_context first."

    def _build_session(
        context: str,
        fmt: ContentFormat,
        line_number_base: LineNumberBase,
    ) -> _Session:
        meta = _analyze_text_context(context, fmt)
        repl = REPLEnvironment(
            context=context,
            context_var_name="ctx",
            config=self.sandbox_config,
            loop=asyncio.get_running_loop(),
        )
        repl.set_variable("line_number_base", line_number_base)
        return _Session(
            repl=repl,
            meta=meta,
            line_number_base=line_number_base,
        )

    def _require_session(
        context_id: str,
        increment: bool = True,
    ) -> _Session | str:
        session = cast(_Session | None, self._sessions.get(context_id))
        if session is None:
            return NO_CONTEXT_ERROR.format(context_id=context_id)
        if increment:
            session.iterations += 1
        return cast(_Session, session)

    def _create_session(
        context: str,
        context_id: str,
        fmt: ContentFormat,
        line_number_base: LineNumberBase,
    ) -> ContextMetadata:
        session = _build_session(context, fmt, line_number_base)
        self._sessions[context_id] = session
        return session.meta

    def _get_or_create_session(
        context_id: str,
        line_number_base: LineNumberBase | None = None,
    ) -> _Session:
        session = cast(_Session | None, self._sessions.get(context_id))
        if session is not None:
            return session

        base = line_number_base if line_number_base is not None else DEFAULT_LINE_NUMBER_BASE
        session = _build_session("", ContentFormat.TEXT, base)
        self._sessions[context_id] = session
        return session

    def _first_doc_line(fn: Any) -> str:
        doc = inspect.getdoc(fn) or ""
        for line in doc.splitlines():
            line = line.strip()
            if line:
                return line
        return ""

    def _short_description(fn: Any, override: str | None) -> str:
        desc = (override or _first_doc_line(fn)).strip()
        if not desc:
            desc = fn.__name__.replace("_", " ")
        max_len = 120
        if len(desc) > max_len:
            desc = desc[: max_len - 3].rstrip() + "..."
        return desc

    def _tool(description: str | None = None, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            doc = inspect.getdoc(fn) or ""
            if self.tool_docs_mode == "full" and doc:
                return self.server.tool(**kwargs)(fn)
            desc = _short_description(fn, description)
            return self.server.tool(description=desc, **kwargs)(fn)

        return decorator

    @_tool()
    async def load_context(
        content: str | None = None,
        context_id: str = "default",
        format: str = "auto",
        line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE,
        context: str | None = None,
    ) -> str:
        """Load context into an in-memory REPL session.

        The context is stored in a sandboxed Python environment as the variable `ctx`.
        You can then use other tools to explore and process this context.

        Args:
            content: The text/data to load
            context_id: Identifier for this context session (default: "default")
            format: Content format - "auto", "text", or "json" (default: "auto")
            line_number_base: Line number base for this context (0 or 1)
            context: Deprecated alias for content

        Returns:
            Confirmation with context metadata
        """
        text = content if content is not None else context
        if text is None:
            return "Error: content is required"
        try:
            base = _validate_line_number_base(line_number_base)
        except ValueError as e:
            return f"Error: {e}"

        fmt = _detect_format(text) if format == "auto" else ContentFormat(format)
        meta = _create_session(text, context_id, fmt, base)
        return _format_context_loaded(context_id, meta, base)

    def _build_memory_pack_payload() -> tuple[dict[str, Any], list[str]]:
        sessions_payload: list[dict[str, Any]] = []
        skipped: list[str] = []
        for sid, sess in self._sessions.items():
            try:
                sessions_payload.append(_session_to_payload(sid, sess))
            except Exception:
                skipped.append(sid)
        payload = {
            "schema": "aleph.memory_pack.v1",
            "created_at": datetime.now().isoformat(),
            "sessions": sessions_payload,
            "skipped": skipped,
        }
        return payload, skipped

    action_deps = ActionDeps(
        action_config=self.action_config,
        get_or_create_session=_get_or_create_session,
        create_session=_create_session,
        scoped_path=_scoped_path,
        load_text_from_path=_load_text_from_path,
    )

    def _auto_save_memory_pack() -> None:
        if not self.action_config.enabled or not self._sessions:
            return
        payload, _ = _build_memory_pack_payload()
        out_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8", errors="replace")
        if len(out_bytes) > self.action_config.max_write_bytes:
            return
        try:
            p = _scoped_path(
                self.action_config.workspace_root,
                MEMORY_PACK_RELATIVE_PATH,
                self.action_config.workspace_mode,
            )
        except Exception:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "wb") as f:
                f.write(out_bytes)
        except Exception:
            return
        for sess in self._sessions.values():
            _actions.record_action(sess, note="auto_save_memory_pack", snippet=str(p))

    @_tool()
    async def run_command(
        cmd: str,
        cwd: str | None = None,
        timeout_seconds: float | None = None,
        shell: bool = False,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        return await _actions.run_command(
            action_deps,
            cmd=cmd,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            shell=shell,
            confirm=confirm,
            output=output,
            context_id=context_id,
        )

    @_tool()
    async def rg_search(
        pattern: str,
        paths: list[str] | None = None,
        glob: str | None = None,
        max_results: int = 200,
        load_context_id: str | None = None,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        """Fast codebase search using ripgrep (rg) with fallback scanning.

        Args:
            pattern: Regex pattern to search for
            paths: Optional list of files/dirs (defaults to workspace root)
            glob: Optional glob filter (e.g. "*.py")
            max_results: Max matches to return (default: 200)
            load_context_id: If set, load matches into this context
            confirm: Required if actions are enabled
            output: "json", "markdown", or "object"
            context_id: Session to record evidence in
        """
        return await _actions.rg_search(
            action_deps,
            pattern=pattern,
            paths=paths,
            glob=glob,
            max_results=max_results,
            load_context_id=load_context_id,
            confirm=confirm,
            output=output,
            context_id=context_id,
        )

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
    ) -> str | dict[str, Any]:
        return await _actions.read_file(
            action_deps,
            path=path,
            start_line=start_line,
            limit=limit,
            include_raw=include_raw,
            line_number_base=line_number_base,
            confirm=confirm,
            output=output,
            context_id=context_id,
        )

    @_tool()
    async def load_file(
        path: str,
        context_id: str = "default",
        format: str = "auto",
        line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE,
        confirm: bool = False,
    ) -> str:
        """Load a workspace file into a context session.

        Args:
            path: File path to read (relative to workspace root)
            context_id: Identifier for this context session (default: "default")
            format: Content format - "auto", "text", or "json" (default: "auto")
            line_number_base: Line number base for this context (0 or 1)
            confirm: Required if actions are enabled

        Returns:
            Confirmation with context metadata
        """
        return await _actions.load_file(
            action_deps,
            path=path,
            context_id=context_id,
            format=format,
            line_number_base=line_number_base,
            confirm=confirm,
        )

    @_tool()
    async def write_file(
        path: str,
        content: str,
        mode: Literal["overwrite", "append"] = "overwrite",
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        return await _actions.write_file(
            action_deps,
            path=path,
            content=content,
            mode=mode,
            confirm=confirm,
            output=output,
            context_id=context_id,
        )

    @_tool()
    async def run_tests(
        runner: Literal["auto", "pytest"] = "auto",
        args: list[str] | None = None,
        cwd: str | None = None,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        return await _actions.run_tests(
            action_deps,
            runner=runner,
            args=args,
            cwd=cwd,
            confirm=confirm,
            output=output,
            context_id=context_id,
        )

    @_tool()
    async def list_contexts(
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        items: list[dict[str, Any]] = []
        for cid, session in self._sessions.items():
            items.append(
                {
                    "context_id": cid,
                    "created_at": session.created_at.isoformat(),
                    "iterations": session.iterations,
                    "format": session.meta.format.value,
                    "size_chars": session.meta.size_chars,
                    "size_lines": session.meta.size_lines,
                    "estimated_tokens": session.meta.size_tokens_estimate,
                    "line_number_base": session.line_number_base,
                    "evidence_count": len(session.evidence),
                }
            )

        payload: dict[str, Any] = {
            "count": len(items),
            "items": sorted(items, key=lambda x: x["context_id"]),
        }
        return _format_payload(payload, output=output)

    @_tool()
    async def diff_contexts(
        a: str,
        b: str,
        context_lines: int = 3,
        max_lines: int = 400,
        output: Literal["markdown", "text"] = "markdown",
    ) -> str:
        if a not in self._sessions:
            return f"Error: No context loaded with ID '{a}'. Use load_context first."
        if b not in self._sessions:
            return f"Error: No context loaded with ID '{b}'. Use load_context first."

        sa = self._sessions[a]
        sb = self._sessions[b]
        sa.iterations += 1
        sb.iterations += 1

        a_ctx = sa.repl.get_variable("ctx")
        b_ctx = sb.repl.get_variable("ctx")
        if not isinstance(a_ctx, str) or not isinstance(b_ctx, str):
            return "Error: diff_contexts currently supports only text contexts"

        a_lines = a_ctx.splitlines(keepends=True)
        b_lines = b_ctx.splitlines(keepends=True)
        diff_iter = difflib.unified_diff(
            a_lines,
            b_lines,
            fromfile=a,
            tofile=b,
            n=max(0, context_lines),
        )
        diff_lines = list(diff_iter)
        truncated = False
        if len(diff_lines) > max(0, max_lines):
            diff_lines = diff_lines[: max(0, max_lines)]
            truncated = True

        diff_text = "".join(diff_lines)
        if truncated:
            diff_text += "\n... (truncated)"

        _actions.record_action(sa, note="diff_contexts", snippet=f"{a} vs {b}")
        _actions.record_action(sb, note="diff_contexts", snippet=f"{a} vs {b}")

        if output == "text":
            return diff_text
        return f"```diff\n{diff_text}\n```"

    @_tool()
    async def save_session(
        session_id: str = "default",
        context_id: str | None = None,
        path: str = "aleph_session.json",
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """Save a session to disk.

        Use context_id="*" or session_id="*" to save all sessions as a memory pack.
        """
        err = _actions.require_actions(self.action_config, confirm)
        if err:
            return _format_error(err, output=output)

        target_id = context_id or session_id
        if target_id in {"*", "all"}:
            payload, skipped = _build_memory_pack_payload()
            pack_path = path if path != "aleph_session.json" else ".aleph/memory_pack.json"
            out_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8", errors="replace")
            if len(out_bytes) > self.action_config.max_write_bytes:
                return _format_error(
                    f"Session file too large to write (>{self.action_config.max_write_bytes} bytes)",
                    output=output,
                )
            try:
                p = _scoped_path(
                    self.action_config.workspace_root,
                    pack_path,
                    self.action_config.workspace_mode,
                )
            except Exception as e:
                return _format_error(str(e), output=output)

            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.write(out_bytes)

            for sess in self._sessions.values():
                _actions.record_action(sess, note="save_memory_pack", snippet=str(p))

            payload_out = {"path": str(p), "bytes_written": len(out_bytes), "sessions": len(payload["sessions"])}
            if skipped:
                payload_out["skipped"] = skipped
            return _format_payload(payload_out, output=output)

        if target_id not in self._sessions:
            return _format_error(f"No context loaded with ID '{target_id}'. Use load_context first.", output=output)

        session = self._sessions[target_id]
        session.iterations += 1

        payload = _session_to_payload(target_id, session)
        out_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8", errors="replace")
        if len(out_bytes) > self.action_config.max_write_bytes:
            return _format_error(
                f"Session file too large to write (>{self.action_config.max_write_bytes} bytes)",
                output=output,
            )

        try:
            p = _scoped_path(
                self.action_config.workspace_root,
                path,
                self.action_config.workspace_mode,
            )
        except Exception as e:
            return _format_error(str(e), output=output)

        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(out_bytes)

        _actions.record_action(session, note="save_session", snippet=str(p))
        return _format_payload({"path": str(p), "bytes_written": len(out_bytes)}, output=output)

    @_tool()
    async def load_session(
        path: str,
        session_id: str | None = None,
        context_id: str | None = None,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """Load a session from disk (supports memory packs)."""
        err = _actions.require_actions(self.action_config, confirm)
        if err:
            return _format_error(err, output=output)

        try:
            p = _scoped_path(
                self.action_config.workspace_root,
                path,
                self.action_config.workspace_mode,
            )
        except Exception as e:
            return _format_error(str(e), output=output)

        if not p.exists() or not p.is_file():
            return _format_error(f"File not found: {path}", output=output)

        data = p.read_bytes()
        if len(data) > self.action_config.max_read_bytes:
            return _format_error(
                f"Session file too large to read (>{self.action_config.max_read_bytes} bytes): {path}",
                output=output,
            )

        try:
            obj = json.loads(data.decode("utf-8", errors="replace"))
        except Exception as e:
            return _format_error(f"Failed to parse JSON: {e}", output=output)

        if not isinstance(obj, dict):
            return _format_error("Invalid session file format", output=output)

        schema = obj.get("schema")
        if schema == "aleph.memory_pack.v1":
            sessions = obj.get("sessions")
            if not isinstance(sessions, list):
                return _format_error("Invalid memory pack format", output=output)
            loaded: list[str] = []
            skipped_existing: list[str] = []
            skipped_invalid = 0
            for payload in sessions:
                if not isinstance(payload, dict):
                    skipped_invalid += 1
                    continue
                file_session_id = payload.get("context_id") or payload.get("session_id")
                resolved_id = str(file_session_id) if file_session_id else f"session_{len(self._sessions) + 1}"
                if resolved_id in self._sessions:
                    skipped_existing.append(resolved_id)
                    continue
                try:
                    session = _session_from_payload(
                        payload,
                        resolved_id,
                        self.sandbox_config,
                        loop=asyncio.get_running_loop(),
                    )
                except Exception:
                    skipped_invalid += 1
                    continue
                self._sessions[resolved_id] = session
                _actions.record_action(session, note="load_memory_pack", snippet=str(p))
                loaded.append(resolved_id)
            return _format_payload(
                {
                    "loaded": loaded,
                    "skipped_existing": skipped_existing,
                    "skipped_invalid": skipped_invalid,
                    "loaded_from": str(p),
                },
                output=output,
            )

        file_session_id = obj.get("context_id") or obj.get("session_id")
        resolved_id = context_id or session_id or (str(file_session_id) if file_session_id else "default")
        try:
            session = _session_from_payload(
                obj,
                resolved_id,
                self.sandbox_config,
                loop=asyncio.get_running_loop(),
            )
        except ValueError as e:
            return _format_error(str(e), output=output)

        self._sessions[resolved_id] = session
        _actions.record_action(session, note="load_session", snippet=str(p))
        return _format_payload(
            {
                "context_id": resolved_id,
                "session_id": resolved_id,
                "line_number_base": session.line_number_base,
                "loaded_from": str(p),
            },
            output=output,
        )

    @_tool()
    async def peek_context(
        start: int = 0,
        end: int | None = None,
        context_id: str = "default",
        unit: Literal["chars", "lines"] = "chars",
        record_evidence: bool = True,
    ) -> str:
        """View a portion of the loaded context.

        Args:
            start: Starting position (chars are 0-indexed; lines use the session line number base)
            end: Ending position (chars: exclusive; lines: inclusive, None = to the end)
            context_id: Context identifier
            unit: "chars" for character slicing, "lines" for line slicing
            record_evidence: Store evidence entry for this peek

        Returns:
            The requested portion of the context
        """
        session_or_err = _require_session(context_id)
        if isinstance(session_or_err, str):
            return session_or_err
        session = session_or_err
        repl = session.repl

        if unit == "chars":
            fn = repl.get_variable("peek")
            if not callable(fn):
                return "Error: peek() helper is not available"
            result = fn(start, end)
        else:
            fn = repl.get_variable("lines")
            if not callable(fn):
                return "Error: lines() helper is not available"
            base = session.line_number_base
            if base == 1 and start == 0:
                start = 1
            if end == 0 and base == 1:
                end = 1
            if start < base:
                return f"Error: start must be >= {base} for line-based peeks"
            if end is not None and end < start:
                return "Error: end must be >= start"
            start_idx = start - base
            end_idx = None if end is None else end - base + 1
            result = fn(start_idx, end_idx)

        # Track evidence for provenance
        evidence_before = len(session.evidence)
        if record_evidence and result:
            if unit == "lines":
                lines_count = result.count("\n") + 1 if result else 0
                end_line = start + max(0, lines_count - 1)
                session.evidence.append(
                    _Evidence(
                        source="peek",
                        line_range=(start, end_line),
                        pattern=None,
                        note=None,
                        snippet=result[:200],
                    )
                )
            else:
                session.evidence.append(
                    _Evidence(
                        source="peek",
                        line_range=None,  # Character ranges don't map to lines easily
                        pattern=None,
                        note=None,
                        snippet=result[:200],
                    )
                )
        session.information_gain.append(len(session.evidence) - evidence_before)

        return f"```\n{result}\n```"

    @_tool()
    async def search_context(
        pattern: str,
        context_id: str = "default",
        max_results: int = 10,
        context_lines: int = 2,
        record_evidence: bool = True,
        evidence_mode: Literal["summary", "all"] = "summary",
    ) -> str:
        """Search the context using regex patterns.

        Args:
            pattern: Regular expression pattern to search for
            context_id: Context identifier
            max_results: Maximum number of matches to return
            context_lines: Number of surrounding lines to include
            record_evidence: Store evidence entries for this search
            evidence_mode: "summary" records one entry, "all" records every match

        Returns:
            Matching lines with surrounding context
        """
        session_or_err = _require_session(context_id)
        if isinstance(session_or_err, str):
            return session_or_err
        session = session_or_err
        repl = session.repl

        fn = repl.get_variable("search")
        if not callable(fn):
            return "Error: search() helper is not available"

        try:
            results = fn(pattern, context_lines=context_lines, max_results=max_results)
        except re.error as e:
            return f"Error: Invalid regex pattern `{pattern}`: {e}"

        if not results:
            return f"No matches found for pattern: `{pattern}`"

        base = session.line_number_base
        total_lines = session.meta.size_lines
        max_line = total_lines if base == 1 else max(0, total_lines - 1)

        def _line_range_for(match_line: int) -> tuple[int, int]:
            if base == 1:
                start = max(1, match_line - context_lines)
                end = min(max_line, match_line + context_lines)
            else:
                start = max(0, match_line - context_lines)
                end = min(max_line, match_line + context_lines)
            return start, end

        # Track evidence for provenance
        evidence_before = len(session.evidence)
        out: list[str] = []
        ranges: list[tuple[int, int]] = []
        for r in results:
            try:
                display_line = r["line_num"]
                line_range = _line_range_for(display_line)
                ranges.append(line_range)
                out.append(f"**Line {display_line}:**\n```\n{r['context']}\n```")
            except Exception:
                out.append(str(r))

        if record_evidence:
            if evidence_mode == "all":
                for r, line_range in zip(results, ranges):
                    session.evidence.append(
                        _Evidence(
                            source="search",
                            line_range=line_range,
                            pattern=pattern,
                            note=None,
                            snippet=r.get("match", "")[:200],
                        )
                    )
            else:
                start = min(r[0] for r in ranges)
                end = max(r[1] for r in ranges)
                session.evidence.append(
                    _Evidence(
                        source="search",
                        line_range=(start, end),
                        pattern=pattern,
                        note=f"{len(results)} match(es) (summary)",
                        snippet=results[0].get("match", "")[:200],
                    )
                )

        # Track information gain
        session.information_gain.append(len(session.evidence) - evidence_before)

        line_desc = "1-based" if base == 1 else "0-based"
        return (
            f"## Search Results for `{pattern}`\n\n"
            f"Found {len(results)} match(es) (line numbers are {line_desc}):\n\n"
            + "\n\n---\n\n".join(out)
        )

    @_tool()
    async def semantic_search(
        query: str,
        context_id: str = "default",
        chunk_size: int = 1000,
        overlap: int = 100,
        top_k: int = 5,
        embed_dim: int = 256,
        record_evidence: bool = True,
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Semantic search over the context using lightweight embeddings.

        Args:
            query: Semantic query
            context_id: Context identifier
            chunk_size: Characters per chunk (default: 1000)
            overlap: Overlap between chunks (default: 100)
            top_k: Number of results to return (default: 5)
            embed_dim: Embedding dimensions (default: 256)
            record_evidence: Store evidence entry for this search
            output: "markdown", "json", or "object"
        """
        session_or_err = _require_session(context_id)
        if isinstance(session_or_err, str):
            return session_or_err
        session = session_or_err
        repl = session.repl

        fn = repl.get_variable("semantic_search")
        if not callable(fn):
            return "Error: semantic_search() helper is not available"

        try:
            results = fn(
                query,
                chunk_size=chunk_size,
                overlap=overlap,
                top_k=top_k,
                embed_dim=embed_dim,
            )
        except Exception as e:
            return f"Error: {e}"

        evidence_before = len(session.evidence)
        if record_evidence and results:
            session.evidence.append(
                _Evidence(
                    source="search",
                    line_range=None,
                    pattern=query,
                    note="semantic_search",
                    snippet=str(results[0].get("preview") or "")[:200],
                )
            )
        session.information_gain.append(len(session.evidence) - evidence_before)

        payload = {
            "context_id": context_id,
            "query": query,
            "count": len(results),
            "results": results,
        }

        session.repl._namespace["last_semantic_search"] = payload

        if output == "object":
            return payload
        if output == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2)

        parts = [
            "## Semantic Search Results",
            f"Query: `{query}`",
            f"Matches: {len(results)}",
        ]
        if results:
            parts.append("")
            for r in results:
                parts.append(
                    f"- Chunk {r['index']} ({r['start_char']}-{r['end_char']}), score {r['score']:.3f}: {r['preview']}"
                )
            parts.append("")
            parts.append("*Use `peek_context(start, end, unit='chars')` for full chunks.*")
        return "\n".join(parts)

    @_tool()
    async def exec_python(
        code: str,
        context_id: str = "default",
    ) -> str:
        """Execute Python code in the sandboxed REPL.

        The loaded context is available as the variable `ctx`.

        Available helpers:
        - peek(start, end): View characters
        - lines(start, end): View lines
        - search(pattern, context_lines=2, max_results=20): Regex search
        - chunk(chunk_size, overlap=0): Split context into chunks
        - semantic_search(query, chunk_size=1000, overlap=100, top_k=5): Meaning-based search
        - embed_text(text, dim=256): Lightweight embedding vector
        - cite(snippet, line_range=None, note=None): Tag evidence for provenance
        - allowed_imports(): List allowed imports in the sandbox
        - is_import_allowed(name): Check if an import is allowed
        - blocked_names(): List forbidden builtin names

        Available imports: re, json, csv, math, statistics, collections,
        itertools, functools, datetime, textwrap, difflib

        Args:
            code: Python code to execute
            context_id: Context identifier

        Returns:
            Execution results (stdout, return value, errors)
        """
        session_or_err = _require_session(context_id)
        if isinstance(session_or_err, str):
            return session_or_err
        session = session_or_err
        repl = session.repl

        # Track evidence count before execution
        evidence_before = len(session.evidence)

        result = await repl.execute_async(code)

        # Collect citations from REPL and convert to evidence
        if repl._citations:
            for citation in repl._citations:
                session.evidence.append(_Evidence(
                    source="manual",
                    line_range=citation["line_range"],
                    pattern=None,
                    note=citation["note"],
                    snippet=citation["snippet"][:200],
                ))
            repl._citations.clear()  # Clear after collecting

        # Track information gain
        session.information_gain.append(len(session.evidence) - evidence_before)

        parts: list[str] = []

        if result.stdout:
            parts.append(f"**Output:**\n```\n{result.stdout}\n```")

        if result.return_value is not None:
            parts.append(f"**Return Value:** `{result.return_value}`")

        if result.variables_updated:
            parts.append(f"**Variables Updated:** {', '.join(f'`{v}`' for v in result.variables_updated)}")

        if result.stderr:
            parts.append(f"**Stderr:**\n```\n{result.stderr}\n```")

        if result.error:
            parts.append(f"**Error:** {result.error}")

        if result.truncated:
            parts.append("*Note: Output was truncated*")

        if not parts:
            parts.append("*(No output)*")

        return "## Execution Result\n\n" + "\n\n".join(parts)

    @_tool()
    async def get_variable(
        name: str,
        context_id: str = "default",
    ) -> str:
        """Retrieve a variable from the REPL namespace.

        Args:
            name: Variable name to retrieve
            context_id: Context identifier

        Returns:
            String representation of the variable's value
        """
        session_or_err = _require_session(context_id, increment=False)
        if isinstance(session_or_err, str):
            return session_or_err
        repl = session_or_err.repl
        # Check if variable exists in namespace (not just if it's None)
        if name not in repl._namespace:
            return f"Variable `{name}` not found in namespace."
        value = repl._namespace[name]

        # Format nicely for complex types
        if isinstance(value, (dict, list)):
            try:
                formatted = json.dumps(value, indent=2, ensure_ascii=False)
                return f"**`{name}`:**\n```json\n{formatted}\n```"
            except Exception:
                return f"**`{name}`:** `{value}`"

        return f"**`{name}`:** `{value}`"

    @_tool()
    async def think(
        question: str,
        context_slice: str | None = None,
        context_id: str = "default",
    ) -> str:
        """Structure a reasoning sub-step.

        Use this when you need to break down a complex problem into
        smaller questions. This tool helps you organize your thinking -
        YOU provide the reasoning, not an external API.

        Args:
            question: The sub-question to reason about
            context_slice: Optional relevant context excerpt
            context_id: Context identifier

        Returns:
            A structured prompt for you to reason through
        """
        if context_id in self._sessions:
            self._sessions[context_id].iterations += 1
            self._sessions[context_id].think_history.append(question)

        parts = [
            "## Reasoning Step",
            "",
            f"**Question:** {question}",
        ]

        if context_slice:
            parts.extend([
                "",
                "**Relevant Context:**",
                "```",
                context_slice[:2000],  # Limit context slice
                "```",
            ])

        parts.extend([
            "",
            "---",
            "",
            "**Your task:** Reason through this step-by-step. Consider:",
            "1. What information do you have?",
            "2. What can you infer?",
            "3. What's the answer to this sub-question?",
            "",
            "*After reasoning, use `exec_python` to verify or `finalize` if done.*",
        ])

        return "\n".join(parts)

    @_tool()
    async def tasks(
        action: Literal["add", "list", "update", "done", "remove"] = "list",
        title: str | None = None,
        task_id: int | None = None,
        status: Literal["todo", "doing", "done"] | None = None,
        note: str | None = None,
        context_id: str = "default",
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Track tasks tied to a context session."""
        session = _get_or_create_session(context_id)
        session.iterations += 1

        valid_statuses = {"todo", "doing", "done"}
        now = datetime.now().isoformat()

        if action == "add":
            if not title:
                return _format_error("title is required for add", output=output)
            session.task_counter += 1
            new_task = {
                "id": session.task_counter,
                "title": title,
                "status": status if status in valid_statuses else "todo",
                "note": note,
                "created_at": now,
                "updated_at": now,
            }
            session.tasks.append(new_task)
        elif action in {"update", "done"}:
            if task_id is None:
                return _format_error("task_id is required for update/done", output=output)
            task: dict[str, Any] | None = None
            for t in session.tasks:
                if t.get("id") == task_id:
                    task = t
                    break
            if task is None:
                return _format_error(f"Task {task_id} not found", output=output)
            if title is not None:
                task["title"] = title
            if action == "done":
                task["status"] = "done"
            elif status in valid_statuses:
                task["status"] = status
            if note is not None:
                task["note"] = note
            task["updated_at"] = now
        elif action == "remove":
            if task_id is None:
                return _format_error("task_id is required for remove", output=output)
            before = len(session.tasks)
            session.tasks = [t for t in session.tasks if t.get("id") != task_id]
            if len(session.tasks) == before:
                return _format_error(f"Task {task_id} not found", output=output)

        counts = {
            "todo": sum(1 for t in session.tasks if t.get("status") == "todo"),
            "doing": sum(1 for t in session.tasks if t.get("status") == "doing"),
            "done": sum(1 for t in session.tasks if t.get("status") == "done"),
        }
        items = sorted(session.tasks, key=lambda t: int(t.get("id", 0)))
        payload = {
            "context_id": context_id,
            "total": len(session.tasks),
            "counts": counts,
            "items": items,
        }

        if output == "object":
            return payload
        if output == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2)

        parts = [
            "## Tasks",
            f"Total: {payload['total']} (todo: {counts['todo']}, doing: {counts['doing']}, done: {counts['done']})",
        ]
        if items:
            parts.append("")
            for task in items:
                note_text = f" — {task['note']}" if task.get("note") else ""
                parts.append(f"- [{task.get('status', 'todo')}] #{task.get('id')}: {task.get('title')}{note_text}")
        return "\n".join(parts)

    @_tool()
    async def get_status(
        context_id: str = "default",
    ) -> str:
        """Get current session status.

        Shows loaded context info, iteration count, variables, and history.

        Args:
            context_id: Context identifier

        Returns:
            Formatted status report
        """
        if context_id not in self._sessions:
            return f"No context loaded with ID '{context_id}'. Use load_context to start."

        session = self._sessions[context_id]
        meta = session.meta
        repl = session.repl

        # Get all user-defined variables (excluding builtins and helpers)
        excluded = {
            "ctx",
            "peek",
            "lines",
            "search",
            "chunk",
            "cite",
            "line_number_base",
            "allowed_imports",
            "is_import_allowed",
            "blocked_names",
            "__builtins__",
        }
        variables = {
            k: type(v).__name__
            for k, v in repl._namespace.items()
            if k not in excluded and not k.startswith("_")
        }

        parts = [
            "## Context Status",
            "",
            f"**Context ID:** `{context_id}`",
            f"**Created:** {session.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Iterations:** {session.iterations}",
            "",
            "### Context Info",
            f"- Format: {meta.format.value}",
            f"- Size: {meta.size_chars:,} characters",
            f"- Lines: {meta.size_lines:,}",
            f"- Est. tokens: ~{meta.size_tokens_estimate:,}",
            f"- Line numbers: {'1-based' if session.line_number_base == 1 else '0-based'}",
        ]

        if variables:
            parts.extend([
                "",
                "### User Variables",
            ])
            for name, vtype in variables.items():
                parts.append(f"- `{name}`: {vtype}")

        if session.think_history:
            parts.extend([
                "",
                "### Reasoning History",
            ])
            for i, q in enumerate(session.think_history[-5:], 1):
                parts.append(f"{i}. {q[:100]}{'...' if len(q) > 100 else ''}")

        if session.tasks:
            counts = {
                "todo": sum(1 for t in session.tasks if t.get("status") == "todo"),
                "doing": sum(1 for t in session.tasks if t.get("status") == "doing"),
                "done": sum(1 for t in session.tasks if t.get("status") == "done"),
            }
            parts.extend([
                "",
                "### Tasks",
                f"- Total: {len(session.tasks)} (todo: {counts['todo']}, doing: {counts['doing']}, done: {counts['done']})",
            ])
            open_tasks = [t for t in session.tasks if t.get("status") in {"todo", "doing"}][:5]
            for t in open_tasks:
                parts.append(f"- #{t.get('id')}: {t.get('title')} ({t.get('status')})")

        # Convergence metrics
        parts.extend([
            "",
            "### Convergence Metrics",
            f"- Evidence collected: {len(session.evidence)}",
        ])

        if session.confidence_history:
            latest_conf = session.confidence_history[-1]
            parts.append(f"- Latest confidence: {latest_conf:.1%}")
            if len(session.confidence_history) >= 2:
                trend = session.confidence_history[-1] - session.confidence_history[-2]
                trend_str = "↑" if trend > 0 else "↓" if trend < 0 else "→"
                parts.append(f"- Confidence trend: {trend_str} ({trend:+.1%})")
            parts.append(f"- Confidence history: {[f'{c:.0%}' for c in session.confidence_history[-5:]]}")

        if session.information_gain:
            total_gain = sum(session.information_gain)
            recent_gain = sum(session.information_gain[-3:]) if len(session.information_gain) >= 3 else total_gain
            parts.append(f"- Total information gain: {total_gain} evidence pieces")
            parts.append(f"- Recent gain (last 3): {recent_gain}")

        if session.chunks:
            parts.append(f"- Chunks mapped: {len(session.chunks)}")

        if session.evidence:
            parts.extend([
                "",
                "*Use `get_evidence()` to view citations.*",
            ])

        return "\n".join(parts)

    @_tool()
    async def get_evidence(
        context_id: str = "default",
        limit: int = 20,
        offset: int = 0,
        source: Literal["any", "search", "peek", "exec", "manual", "action"] = "any",
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Retrieve collected evidence/citations for a session.

        Args:
            context_id: Context identifier
            limit: Max number of evidence items to return (default: 20)
            offset: Starting index (default: 0)
            source: Optional source filter (default: "any")
            output: "markdown" or "json" (default: "markdown")

        Returns:
            Evidence list, formatted for inspection or programmatic parsing.
        """
        session_or_err = _require_session(context_id, increment=False)
        if isinstance(session_or_err, str):
            return session_or_err
        session = session_or_err
        evidence = session.evidence
        if source != "any":
            evidence = [e for e in evidence if e.source == source]

        total = len(evidence)
        offset = max(0, offset)
        limit = 20 if limit <= 0 else limit

        page = evidence[offset : offset + limit]

        if output in {"json", "object"}:
            payload_items = [
                {
                    "index": offset + i,
                    "source": ev.source,
                    "line_range": ev.line_range,
                    "pattern": ev.pattern,
                    "note": ev.note,
                    "snippet": ev.snippet,
                    "timestamp": ev.timestamp.isoformat(),
                }
                for i, ev in enumerate(page, 1)
            ]
            payload = {
                "context_id": context_id,
                "total": total,
                "line_number_base": session.line_number_base,
                "items": payload_items,
            }
            if output == "object":
                return payload
            return json.dumps(payload, ensure_ascii=False, indent=2)

        parts = [
            "## Evidence",
            "",
            f"**Context ID:** `{context_id}`",
            f"**Total items:** {total}",
            f"**Showing:** {len(page)} (offset={offset}, limit={limit})",
            f"**Line numbers:** {'1-based' if session.line_number_base == 1 else '0-based'}",
        ]
        if source != "any":
            parts.append(f"**Source filter:** `{source}`")
        parts.append("")

        if not page:
            parts.append("*(No evidence collected yet)*")
            return "\n".join(parts)

        for i, ev in enumerate(page, offset + 1):
            source_info = f"[{ev.source}]"
            if ev.line_range:
                source_info += f" lines {ev.line_range[0]}-{ev.line_range[1]}"
            if ev.pattern:
                source_info += f" pattern: `{ev.pattern}`"
            if ev.note:
                source_info += f" note: {ev.note}"
            snippet = ev.snippet.strip()
            parts.append(f"{i}. {source_info}: \"{snippet}\"")

        return "\n".join(parts)

    @_tool()
    async def finalize(
        answer: str,
        confidence: Literal["high", "medium", "low"] = "medium",
        reasoning_summary: str | None = None,
        context_id: str = "default",
    ) -> str:
        """Mark the task complete with your final answer.

        Use this when you have arrived at your final answer after
        exploring the context and reasoning through the problem.

        Args:
            answer: Your final answer
            confidence: How confident you are (high/medium/low)
            reasoning_summary: Optional brief summary of your reasoning
            context_id: Context identifier

        Returns:
            Formatted final answer
        """
        parts = [
            "## Final Answer",
            "",
            answer,
        ]

        if reasoning_summary:
            parts.extend([
                "",
                "---",
                "",
                f"**Reasoning:** {reasoning_summary}",
            ])

        if context_id in self._sessions:
            session = self._sessions[context_id]
            parts.extend([
                "",
                f"*Completed after {session.iterations} iterations.*",
            ])

        parts.append(f"\n**Confidence:** {confidence}")

        # Add evidence citations if available
        if context_id in self._sessions:
            session = self._sessions[context_id]
            if session.evidence:
                parts.extend([
                    "",
                    "---",
                    "",
                    "### Evidence Citations",
                    f"*Line numbers are {'1-based' if session.line_number_base == 1 else '0-based'}.*",
                ])
                for i, ev in enumerate(session.evidence[-10:], 1):  # Last 10 pieces of evidence
                    source_info = f"[{ev.source}]"
                    if ev.line_range:
                        source_info += f" lines {ev.line_range[0]}-{ev.line_range[1]}"
                    if ev.pattern:
                        source_info += f" pattern: `{ev.pattern}`"
                    if ev.note:
                        source_info += f" note: {ev.note}"
                    parts.append(f"{i}. {source_info}: \"{ev.snippet[:80]}...\"" if len(ev.snippet) > 80 else f"{i}. {source_info}: \"{ev.snippet}\"")

        _auto_save_memory_pack()
        return "\n".join(parts)

    # =====================================================================
    # Sub-query tool (RLM-style recursive reasoning)
    # =====================================================================

    @_tool()
    async def sub_query(
        prompt: str,
        context_slice: str | None = None,
        context_id: str = "default",
        backend: str = "auto",
    ) -> str:
        """Run a sub-query using a spawned sub-agent (RLM-style recursive reasoning).

        This enables you to break large problems into chunks and query a sub-agent
        for each chunk, then aggregate results. The sub-agent runs independently
        and returns its response.

        Backend priority (when backend="auto"):
        1. codex CLI - if installed
        2. gemini CLI - if installed
        3. claude CLI - if installed (deprioritized: hangs in MCP/sandbox contexts)
        4. API - if ALEPH_SUB_QUERY_API_KEY or OPENAI_API_KEY is set (last resort)

        Configure via environment:
        - ALEPH_SUB_QUERY_BACKEND: Force specific backend ("api", "claude", "codex", "gemini")
        - ALEPH_SUB_QUERY_API_KEY or OPENAI_API_KEY: API credentials
        - ALEPH_SUB_QUERY_URL or OPENAI_BASE_URL: Custom endpoint for OpenAI-compatible APIs
        - ALEPH_SUB_QUERY_MODEL: Model name (required)
        - ALEPH_SUB_QUERY_SHARE_SESSION: "true"/"false" to share the live MCP session with CLI sub-agents
        - ALEPH_SUB_QUERY_HTTP_HOST: Host for the streamable HTTP server (default: 127.0.0.1)
        - ALEPH_SUB_QUERY_HTTP_PORT: Port for the streamable HTTP server (default: 8765)
        - ALEPH_SUB_QUERY_HTTP_PATH: Path for the streamable HTTP server (default: /mcp)
        - ALEPH_SUB_QUERY_MCP_SERVER_NAME: MCP server name exposed to sub-agents (default: aleph_shared)

        Args:
            prompt: The question/task for the sub-agent
            context_slice: Optional context to include (e.g., a chunk from ctx).
                If not provided, automatically uses the context from context_id session.
            context_id: Session to use. If context_slice is not provided, the session's
                loaded context is automatically passed to the sub-agent.
            backend: "auto", "claude", "codex", "gemini", or "api"

        Returns:
            The sub-agent's response

        Example usage in exec_python:
            chunks = chunk(100000)  # 100k char chunks
            summaries = []
            for c in chunks:
                result = sub_query("Summarize this section:", context_slice=c)
                summaries.append(result)
            final = sub_query(f"Combine these summaries: {summaries}")
        """
        session = self._sessions.get(context_id)
        if session:
            session.iterations += 1

        # Auto-inject context from session if context_slice not provided
        # This matches the RLM pattern: if you specify a context_id, the sub-agent
        # should have access to that context without needing to pass it explicitly.
        if not context_slice and session:
            ctx_val = session.repl.get_variable("ctx")
            if ctx_val is not None:
                context_slice = _coerce_context_to_text(ctx_val)

        # Truncate context if needed
        truncated = False
        if context_slice and len(context_slice) > self.sub_query_config.max_context_chars:
            context_slice = context_slice[:self.sub_query_config.max_context_chars]
            truncated = True

        # Resolve backend
        resolved_backend = backend
        if backend == "auto":
            resolved_backend = detect_backend(self.sub_query_config)

        allowed_backends = {"auto", "api", *CLI_BACKENDS}
        if resolved_backend not in allowed_backends:
            return f"Error: Unsupported backend '{resolved_backend}'."

        try:
            # Try CLI first, fall back to API
            if resolved_backend in CLI_BACKENDS:
                mcp_server_url = None
                share_session = _get_env_bool("ALEPH_SUB_QUERY_SHARE_SESSION", False)
                if share_session and resolved_backend in {"claude", "codex", "gemini"}:
                    host = os.environ.get("ALEPH_SUB_QUERY_HTTP_HOST", "127.0.0.1")
                    port = _get_env_int("ALEPH_SUB_QUERY_HTTP_PORT", 8765)
                    path = os.environ.get("ALEPH_SUB_QUERY_HTTP_PATH", "/mcp")
                    server_name = os.environ.get(
                        "ALEPH_SUB_QUERY_MCP_SERVER_NAME",
                        "aleph_shared",
                    ).strip() or "aleph_shared"
                    ok, url_or_err = await self._ensure_streamable_http_server(host, port, path)
                    if not ok:
                        return (
                            "## Sub-Query Error\n\n"
                            f"**Backend:** `{resolved_backend}`\n\n"
                            f"Failed to start streamable HTTP server: {url_or_err}"
                        )
                    mcp_server_url = url_or_err
                    prompt = (
                        f"{prompt}\n\n"
                        f"[MCP tools are available via the live Aleph server. "
                        f"Use context_id={context_id!r} when calling tools. "
                        f"Tools are prefixed with `mcp__{server_name}__`.]"
                    )
                success, output = await run_cli_sub_query(
                    prompt=prompt,
                    context_slice=context_slice,
                    backend=resolved_backend,  # type: ignore
                    timeout=self.sub_query_config.cli_timeout_seconds,
                    cwd=self.action_config.workspace_root if self.action_config.enabled else None,
                    max_output_chars=self.sub_query_config.cli_max_output_chars,
                    mcp_server_url=mcp_server_url,
                    mcp_server_name=server_name if mcp_server_url else "aleph_shared",
                    trust_mcp_server=True,
                )
            else:
                success, output = await run_api_sub_query(
                    prompt=prompt,
                    context_slice=context_slice,
                    model=self.sub_query_config.api_model,
                    api_key_env=self.sub_query_config.api_key_env,
                    api_base_url_env=self.sub_query_config.api_base_url_env,
                    api_model_env=self.sub_query_config.api_model_env,
                    timeout=self.sub_query_config.api_timeout_seconds,
                    system_prompt=self.sub_query_config.system_prompt if self.sub_query_config.include_system_prompt else None,
                )
        except Exception as e:
            success = False
            output = f"{type(e).__name__}: {e}"

        # Record evidence
        if session:
            session.evidence.append(_Evidence(
                source="sub_query",
                line_range=None,
                pattern=None,
                snippet=output[:200] if success else f"[ERROR] {output[:150]}",
                note=f"backend={resolved_backend}" + (" [truncated context]" if truncated else ""),
            ))
            session.information_gain.append(1 if success else 0)

        if not success:
            return f"## Sub-Query Error\n\n**Backend:** `{resolved_backend}`\n\n{output}"

        parts = [
            "## Sub-Query Result",
            "",
            f"**Backend:** `{resolved_backend}`",
        ]
        if truncated:
            parts.append(f"*Note: Context was truncated to {self.sub_query_config.max_context_chars:,} chars*")
        parts.extend(["", "---", "", output])

        return "\n".join(parts)

    # =====================================================================
    # Remote MCP orchestration (v0.5 last mile)
    # =====================================================================

    @_tool()
    async def add_remote_server(
        server_id: str,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        allow_tools: list[str] | None = None,
        deny_tools: list[str] | None = None,
        connect: bool = True,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Register a remote MCP server (stdio transport) for orchestration.

        This spawns a subprocess and speaks MCP over stdin/stdout.

        Args:
            server_id: Local identifier for the remote server
            command: Executable to run (e.g. 'python3')
            args: Command arguments (e.g. ['-m','some.mcp.server'])
            cwd: Working directory for the subprocess
            env: Extra environment variables for the subprocess
            allow_tools: Optional allowlist of tool names
            deny_tools: Optional denylist of tool names
            connect: If true, connect immediately and cache tool list
            confirm: Required if actions are enabled
            output: Output format
        """
        err = _actions.require_actions(self.action_config, confirm)
        if err:
            return _format_error(err, output=output)

        if server_id in self._remote_servers:
            return _format_error(f"Remote server '{server_id}' already exists.", output=output)

        handle = _RemoteServerHandle(
            command=command,
            args=args or [],
            cwd=Path(cwd) if cwd else None,
            env=env,
            allow_tools=allow_tools,
            deny_tools=deny_tools,
        )
        self._remote_servers[server_id] = handle

        tools: list[dict[str, Any]] | None = None
        if connect:
            ok, res = await self._remote_orchestrator.ensure_remote_server(server_id)
            if not ok:
                return _format_error(str(res), output=output)
            if not isinstance(res, _RemoteServerHandle):
                return _format_error(str(res), output=output)
            handle = res
            session = handle.session
            if session is None:
                tools = None
            else:
                try:
                    r = await session.list_tools()
                    tools = _to_jsonable(r)
                except Exception:
                    tools = None

        payload: dict[str, Any] = {
            "server_id": server_id,
            "command": command,
            "args": args or [],
            "cwd": str(handle.cwd) if handle.cwd else None,
            "allow_tools": allow_tools,
            "deny_tools": deny_tools,
            "connected": handle.session is not None,
            "tools": tools,
        }
        return _format_payload(payload, output=output)

    @_tool()
    async def list_remote_servers(
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """List all registered remote MCP servers."""
        items = []
        for sid, h in self._remote_servers.items():
            items.append(
                {
                    "server_id": sid,
                    "command": h.command,
                    "args": h.args,
                    "cwd": str(h.cwd) if h.cwd else None,
                    "connected": h.session is not None,
                    "connected_at": h.connected_at.isoformat() if h.connected_at else None,
                    "allow_tools": h.allow_tools,
                    "deny_tools": h.deny_tools,
                }
            )
        return _format_payload({"count": len(items), "items": items}, output=output)

    @_tool()
    async def list_remote_tools(
        server_id: str,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """List tools available on a remote MCP server."""
        err = _actions.require_actions(self.action_config, confirm)
        if err:
            return _format_error(err, output=output)

        ok, res = await self._remote_orchestrator.ensure_remote_server(server_id)
        if not ok:
            return _format_error(str(res), output=output)
        ok2, tools = await self._remote_orchestrator.remote_list_tools(server_id)
        if not ok2:
            return _format_error(str(tools), output=output)
        return _format_payload(tools, output=output)

    @_tool()
    async def call_remote_tool(
        server_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: float | None = DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "markdown",
    ) -> str | dict[str, Any] | list[Any]:
        """Call a tool on a remote MCP server.

        Args:
            server_id: Registered remote server ID
            tool: Tool name
            arguments: Tool arguments object
            timeout_seconds: Tool call timeout (best-effort). Defaults to ALEPH_REMOTE_TOOL_TIMEOUT or 120s.
            confirm: Required if actions are enabled
            output: Output format
        """
        err = _actions.require_actions(self.action_config, confirm)
        if err:
            return _format_error(err, output=output)

        ok, res = await self._remote_orchestrator.ensure_remote_server(server_id)
        if not ok:
            return _format_error(str(res), output=output)
        ok2, result_jsonable = await self._remote_orchestrator.remote_call_tool(
            server_id=server_id,
            tool=tool,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
        )
        if not ok2:
            return _format_error(str(result_jsonable), output=output)

        if output == "object":
            return cast(dict[str, Any] | list[Any], result_jsonable)
        if output == "json":
            return json.dumps(result_jsonable, ensure_ascii=False, indent=2)

        parts = [
            "## Remote Tool Result",
            "",
            f"**Server:** `{server_id}`",
            f"**Tool:** `{tool}`",
            "",
            "```json",
            json.dumps(result_jsonable, ensure_ascii=False, indent=2)[:10_000],
            "```",
        ]
        return "\n".join(parts)

    @_tool()
    async def close_remote_server(
        server_id: str,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Close a remote MCP server connection (terminates subprocess)."""
        err = _actions.require_actions(self.action_config, confirm)
        if err:
            return _format_error(err, output=output)

        ok, msg = await self._remote_orchestrator.close_remote_server(server_id)
        msg_text = str(msg)
        if output == "object":
            return {"ok": ok, "message": msg_text}
        if output == "json":
            return json.dumps({"ok": ok, "message": msg_text}, indent=2)
        return msg_text

    @_tool()
    async def chunk_context(
        chunk_size: int = 2000,
        overlap: int = 200,
        context_id: str = "default",
    ) -> str:
        """Split context into chunks and return metadata for navigation.

        Use this to understand how to navigate large documents systematically.
        Returns chunk boundaries so you can peek specific chunks.

        Args:
            chunk_size: Characters per chunk (default: 2000)
            overlap: Overlap between chunks (default: 200)
            context_id: Context identifier

        Returns:
            JSON with chunk metadata (index, start_char, end_char, preview)
        """
        session_or_err = _require_session(context_id)
        if isinstance(session_or_err, str):
            return session_or_err
        session = session_or_err
        repl = session.repl

        fn = repl.get_variable("chunk")
        if not callable(fn):
            return "Error: chunk() helper is not available"

        try:
            chunks = fn(chunk_size, overlap)
        except ValueError as e:
            return f"Error: {e}"

        # Build chunk metadata
        chunk_meta = []
        pos = 0
        for i, chunk_text in enumerate(chunks):
            chunk_meta.append({
                "index": i,
                "start_char": pos,
                "end_char": pos + len(chunk_text),
                "size": len(chunk_text),
                "preview": chunk_text[:100] + "..." if len(chunk_text) > 100 else chunk_text,
            })
            pos += len(chunk_text) - overlap if i < len(chunks) - 1 else len(chunk_text)

        # Store in session for reference
        session.chunks = chunk_meta

        parts = [
            "## Context Chunks",
            "",
            f"**Total chunks:** {len(chunks)}",
            f"**Chunk size:** {chunk_size} chars",
            f"**Overlap:** {overlap} chars",
            "",
            "### Chunk Map",
            "",
        ]

        for cm in chunk_meta:
            parts.append(f"- **Chunk {cm['index']}** ({cm['start_char']}-{cm['end_char']}): {cm['preview'][:60]}...")

        parts.extend([
            "",
            "*Use `peek_context(start, end, unit='chars')` to view specific chunks.*",
        ])

        return "\n".join(parts)

    @_tool()
    async def evaluate_progress(
        current_understanding: str,
        remaining_questions: list[str] | str | None = None,
        confidence_score: float = 0.5,
        context_id: str = "default",
    ) -> str:
        """Self-evaluate your progress to decide whether to continue or finalize.

        Use this periodically to assess whether you have enough information
        to answer the question, or if more exploration is needed.

        Args:
            current_understanding: Summary of what you've learned so far
            remaining_questions: List of unanswered questions (if any)
            confidence_score: Your confidence 0.0-1.0 in current understanding
            context_id: Context identifier

        Returns:
            Structured evaluation with recommendation (continue/finalize)
        """
        if isinstance(remaining_questions, str):
            remaining_questions = [remaining_questions]
        if context_id in self._sessions:
            session = self._sessions[context_id]
            session.iterations += 1
            session.confidence_history.append(confidence_score)

        parts = [
            "## Progress Evaluation",
            "",
            "**Current Understanding:**",
            current_understanding,
            "",
        ]

        if remaining_questions:
            parts.extend([
                "**Remaining Questions:**",
            ])
            for q in remaining_questions:
                parts.append(f"- {q}")
            parts.append("")

        parts.append(f"**Confidence Score:** {confidence_score:.1%}")

        # Analyze convergence
        if context_id in self._sessions:
            session = self._sessions[context_id]
            parts.extend([
                "",
                "### Convergence Analysis",
                f"- Iterations: {session.iterations}",
                f"- Evidence collected: {len(session.evidence)}",
            ])

            if len(session.confidence_history) >= 2:
                trend = session.confidence_history[-1] - session.confidence_history[-2]
                trend_str = "increasing" if trend > 0 else "decreasing" if trend < 0 else "stable"
                parts.append(f"- Confidence trend: {trend_str} ({trend:+.1%})")

            if session.information_gain:
                recent_gain = sum(session.information_gain[-3:]) if len(session.information_gain) >= 3 else sum(session.information_gain)
                parts.append(f"- Recent information gain: {recent_gain} evidence pieces (last 3 ops)")

        # Recommendation
        parts.extend([
            "",
            "---",
            "",
            "### Recommendation",
        ])

        if confidence_score >= 0.8:
            parts.append("**READY TO FINALIZE** - High confidence achieved. Use `finalize()` to provide your answer.")
        elif confidence_score >= 0.5 and not remaining_questions:
            parts.append("**CONSIDER FINALIZING** - Moderate confidence with no remaining questions. You may finalize or continue exploring.")
        else:
            parts.append("**CONTINUE EXPLORING** - More investigation needed. Use `search_context`, `peek_context`, or `think` to gather more evidence.")

        return "\n".join(parts)

    @_tool()
    async def summarize_so_far(
        include_evidence: bool = True,
        include_variables: bool = True,
        clear_history: bool = False,
        context_id: str = "default",
    ) -> str:
        """Compress reasoning history to manage context window.

        Use this when your conversation is getting long to create a
        condensed summary of your progress that can replace earlier context.

        Args:
            include_evidence: Include evidence citations in summary
            include_variables: Include computed variables
            clear_history: Clear think_history after summarizing (to save memory)
            context_id: Context identifier

        Returns:
            Compressed reasoning trace
        """
        session_or_err = _require_session(context_id, increment=False)
        if isinstance(session_or_err, str):
            return session_or_err
        session = session_or_err

        parts = [
            "## Context Summary",
            "",
            f"**Context ID:** `{context_id}`",
            f"**Duration:** {datetime.now() - session.created_at}",
            f"**Iterations:** {session.iterations}",
            "",
        ]

        # Reasoning history
        if session.think_history:
            parts.extend([
                "### Reasoning Steps",
            ])
            for i, q in enumerate(session.think_history[-5:], 1):
                parts.append(f"{i}. {q[:150]}{'...' if len(q) > 150 else ''}")
            parts.append("")

        if session.tasks:
            counts = {
                "todo": sum(1 for t in session.tasks if t.get("status") == "todo"),
                "doing": sum(1 for t in session.tasks if t.get("status") == "doing"),
                "done": sum(1 for t in session.tasks if t.get("status") == "done"),
            }
            parts.extend([
                "### Tasks",
                f"Total: {len(session.tasks)} (todo: {counts['todo']}, doing: {counts['doing']}, done: {counts['done']})",
            ])
            for t in session.tasks[:5]:
                parts.append(f"- #{t.get('id')}: {t.get('title')} ({t.get('status')})")
            parts.append("")

        # Evidence summary
        if include_evidence and session.evidence:
            parts.extend([
                "### Evidence Collected",
                f"Total: {len(session.evidence)} pieces",
                "",
            ])
            # Group by source
            by_source: dict[str, int] = {}
            for ev in session.evidence:
                by_source[ev.source] = by_source.get(ev.source, 0) + 1
            for source, count in by_source.items():
                parts.append(f"- {source}: {count}")
            parts.append("")

            # Show key evidence
            parts.append("**Key Evidence:**")
            for ev in session.evidence[-5:]:  # Last 5
                snippet = ev.snippet[:100] + ("..." if len(ev.snippet) > 100 else "")
                note = f" (note: {ev.note})" if ev.note else ""
                parts.append(f"- [{ev.source}] {snippet}{note}")
            parts.append("")

        # Variables
        if include_variables:
            repl = session.repl
            excluded = {
                "ctx",
                "peek",
                "lines",
                "search",
                "chunk",
                "cite",
                "line_number_base",
                "allowed_imports",
                "is_import_allowed",
                "blocked_names",
                "__builtins__",
            }
            variables = {
                k: v for k, v in repl._namespace.items()
                if k not in excluded and not k.startswith("_")
            }
            if variables:
                parts.extend([
                    "### Computed Variables",
                ])
                for name, val in variables.items():
                    val_str = str(val)[:100]
                    parts.append(f"- `{name}` = {val_str}{'...' if len(str(val)) > 100 else ''}")
                parts.append("")

        # Convergence
        if session.confidence_history:
            latest = session.confidence_history[-1]
            parts.extend([
                "### Convergence Status",
                f"- Latest confidence: {latest:.1%}",
                f"- Confidence history: {[f'{c:.0%}' for c in session.confidence_history[-5:]]}",
            ])

        # Clear history if requested
        if clear_history:
            session.think_history = []
            parts.extend([
                "",
                "*Reasoning history cleared to save memory.*",
            ])

        return "\n".join(parts)

"""API-free MCP server for use with Claude Desktop, Cursor, Windsurf, etc.

This server exposes Aleph's context exploration tools WITHOUT requiring
external API calls. The host AI (Claude, GPT, etc.) provides the reasoning.

Tools:
- load_context: Load text/data into sandboxed REPL
- peek_context: View character/line ranges
- search_context: Regex search with context
- exec_python: Execute Python code in sandbox
- get_variable: Retrieve variables from REPL
- sub_query: RLM-style recursive sub-agent queries (CLI or API backend)
- think: Structure a reasoning sub-step (returns prompt for YOU to reason about)
- get_status: Show current session state
- get_evidence: Retrieve collected evidence/citations
- finalize: Mark task complete with answer
- chunk_context: Split context into chunks with metadata for navigation
- evaluate_progress: Self-evaluate progress with convergence tracking
- summarize_so_far: Compress reasoning history to manage context window

Usage:
    python -m aleph.mcp.local_server

Or via entry point:
    aleph-mcp-local
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import difflib
import json
import re
import shlex
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from ..repl.sandbox import REPLEnvironment, SandboxConfig
from ..types import ContentFormat, ContextMetadata
from ..recipe import (
    RecipeConfig,
    RecipeRunner,
    RecipeResult,
    RecipeMetrics,
    EvidenceBundle,
    EvidenceItem,
    DatasetInput,
    load_alephfile,
    save_alephfile,
    hash_content,
    compute_baseline_tokens,
    SCHEMA_VERSION,
)
from ..sub_query import SubQueryConfig, detect_backend, has_api_credentials
from ..sub_query.cli_backend import run_cli_sub_query, CLI_BACKENDS
from ..sub_query.api_backend import run_api_sub_query

__all__ = ["AlephMCPServerLocal", "main"]


LineNumberBase = Literal[0, 1]
DEFAULT_LINE_NUMBER_BASE: LineNumberBase = 1


@dataclass
class _Evidence:
    """Provenance tracking for reasoning conclusions."""
    source: Literal["search", "peek", "exec", "manual", "action", "sub_query"]
    line_range: tuple[int, int] | None
    pattern: str | None
    snippet: str
    note: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


def _detect_format(text: str) -> ContentFormat:
    """Detect content format from text."""
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        try:
            json.loads(text)
            return ContentFormat.JSON
        except Exception:
            return ContentFormat.TEXT
    return ContentFormat.TEXT


def _analyze_text_context(text: str, fmt: ContentFormat) -> ContextMetadata:
    """Analyze text and return metadata."""
    return ContextMetadata(
        format=fmt,
        size_bytes=len(text.encode("utf-8", errors="ignore")),
        size_chars=len(text),
        size_lines=text.count("\n") + 1,
        size_tokens_estimate=len(text) // 4,
        structure_hint=None,
        sample_preview=text[:500],
    )


@dataclass
class _Session:
    """Session state for a context."""
    repl: REPLEnvironment
    meta: ContextMetadata
    line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE
    created_at: datetime = field(default_factory=datetime.now)
    iterations: int = 0
    think_history: list[str] = field(default_factory=list)
    # Provenance tracking
    evidence: list[_Evidence] = field(default_factory=list)
    # Convergence signals
    confidence_history: list[float] = field(default_factory=list)
    information_gain: list[int] = field(default_factory=list)  # evidence count per iteration
    # Chunk metadata for navigation
    chunks: list[dict] | None = None


def _detect_workspace_root() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    return cwd


def _scoped_path(workspace_root: Path, path: str) -> Path:
    root = workspace_root.resolve()
    p = Path(path)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (root / p).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path '{path}' escapes workspace root '{root}'")
    return resolved


def _format_payload(
    payload: dict[str, Any],
    output: Literal["json", "markdown", "object"],
) -> str | dict[str, Any]:
    if output == "object":
        return payload
    if output == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def _format_error(
    message: str,
    output: Literal["json", "markdown", "object"],
) -> str | dict[str, Any]:
    if output == "markdown":
        return f"Error: {message}"
    return _format_payload({"error": message}, output=output)


def _validate_line_number_base(value: int) -> LineNumberBase:
    if value not in (0, 1):
        raise ValueError("line_number_base must be 0 or 1")
    return cast(LineNumberBase, value)


def _resolve_line_number_base(
    session: _Session | None,
    value: int | None,
) -> LineNumberBase:
    if session is not None:
        if value is None:
            return session.line_number_base
        base = _validate_line_number_base(value)
        if base != session.line_number_base:
            raise ValueError("line_number_base does not match existing session")
        return base
    if value is None:
        return DEFAULT_LINE_NUMBER_BASE
    return _validate_line_number_base(value)

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


@dataclass(slots=True)
class ActionConfig:
    enabled: bool = False
    workspace_root: Path = field(default_factory=_detect_workspace_root)
    require_confirmation: bool = False
    max_cmd_seconds: float = 30.0
    max_output_chars: int = 50_000
    max_read_bytes: int = 1_000_000
    max_write_bytes: int = 1_000_000


@dataclass
class _RemoteServerHandle:
    """A managed remote MCP server connection (stdio transport)."""

    command: str
    args: list[str] = field(default_factory=list)
    cwd: Path | None = None
    env: dict[str, str] | None = None
    allow_tools: list[str] | None = None
    deny_tools: list[str] | None = None

    connected_at: datetime | None = None
    session: Any | None = None  # ClientSession (kept as Any to avoid hard dependency at import time)
    _stack: AsyncExitStack | None = None


class AlephMCPServerLocal:
    """API-free MCP server for local AI reasoning.

    This server provides context exploration tools that work with any
    MCP-compatible AI host (Claude Desktop, Cursor, Windsurf, etc.).

    The key difference from AlephMCPServer: NO external API calls.
    The host AI provides all the reasoning.
    """

    def __init__(
        self,
        sandbox_config: SandboxConfig | None = None,
        action_config: ActionConfig | None = None,
        sub_query_config: SubQueryConfig | None = None,
    ) -> None:
        self.sandbox_config = sandbox_config or SandboxConfig()
        self.action_config = action_config or ActionConfig()
        self.sub_query_config = sub_query_config or SubQueryConfig()
        self._sessions: dict[str, _Session] = {}
        self._recipes: dict[str, RecipeRunner] = {}
        self._recipe_results: dict[str, RecipeResult] = {}
        self._remote_servers: dict[str, _RemoteServerHandle] = {}

        # Import MCP lazily so it's an optional dependency
        try:
            from mcp.server.fastmcp import FastMCP
        except Exception as e:
            raise RuntimeError(
                "MCP support requires the `mcp` package. Install with `pip install aleph[mcp]`."
            ) from e

        self.server = FastMCP("aleph-local")
        self._register_tools()

    async def _ensure_remote_server(self, server_id: str) -> tuple[bool, str | _RemoteServerHandle]:
        """Ensure a remote MCP server is connected and initialized."""
        if server_id not in self._remote_servers:
            return False, f"Error: Remote server '{server_id}' not registered."

        handle = self._remote_servers[server_id]
        if handle.session is not None:
            return True, handle

        try:
            from mcp.client.session import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except Exception as e:  # pragma: no cover
            return False, f"Error: MCP client support is not available: {e}"

        params = StdioServerParameters(
            command=handle.command,
            args=handle.args,
            env=handle.env,
            cwd=str(handle.cwd) if handle.cwd is not None else None,
        )

        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
        except Exception as e:
            await stack.aclose()
            return False, f"Error: Failed to connect to remote server '{server_id}': {e}"

        handle._stack = stack
        handle.session = session
        handle.connected_at = datetime.now()
        return True, handle

    async def _close_remote_server(self, server_id: str) -> tuple[bool, str]:
        """Close a remote server connection and terminate the subprocess."""
        if server_id not in self._remote_servers:
            return False, f"Error: Remote server '{server_id}' not registered."

        handle = self._remote_servers[server_id]
        if handle._stack is not None:
            try:
                await handle._stack.aclose()
            finally:
                handle._stack = None
                handle.session = None
                handle.connected_at = None
        return True, f"Closed remote server '{server_id}'."

    async def _remote_list_tools(self, server_id: str) -> tuple[bool, Any]:
        ok, res = await self._ensure_remote_server(server_id)
        if not ok:
            return False, res
        handle = res  # type: ignore[assignment]
        try:
            result = await handle.session.list_tools()  # type: ignore[union-attr]
            return True, _to_jsonable(result)
        except Exception as e:
            return False, f"Error: list_tools failed: {e}"

    async def _remote_call_tool(
        self,
        server_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: float | None = 30.0,
        recipe_id: str | None = None,
    ) -> tuple[bool, Any]:
        ok, res = await self._ensure_remote_server(server_id)
        if not ok:
            return False, res
        handle = res  # type: ignore[assignment]

        if not self._remote_tool_allowed(handle, tool):
            return False, f"Error: Tool '{tool}' is not allowed for remote server '{server_id}'."

        try:
            from datetime import timedelta

            read_timeout = timedelta(seconds=float(timeout_seconds or 30.0))
            result = await handle.session.call_tool(  # type: ignore[union-attr]
                name=tool,
                arguments=arguments or {},
                read_timeout_seconds=read_timeout,
            )
        except Exception as e:
            return False, f"Error: call_tool failed: {e}"

        result_jsonable = _to_jsonable(result)

        if recipe_id and recipe_id in self._recipes:
            runner = self._recipes[recipe_id]
            runner.record_trace(
                tool=f"remote:{server_id}:{tool}",
                args={"server_id": server_id, "tool": tool, "arguments": arguments or {}},
                result=result_jsonable,
            )
            runner.add_evidence(
                source="remote",
                snippet=json.dumps(result_jsonable, ensure_ascii=False)[:500],
                pattern=tool,
                note=f"remote server={server_id}",
                dataset_id=f"remote:{server_id}",
            )

        return True, result_jsonable

    def _remote_tool_allowed(self, handle: _RemoteServerHandle, tool_name: str) -> bool:
        if handle.allow_tools is not None:
            return tool_name in handle.allow_tools
        if handle.deny_tools is not None and tool_name in handle.deny_tools:
            return False
        return True

    def _register_tools(self) -> None:
        """Register all MCP tools."""

        def _format_context_loaded(
            context_id: str,
            meta: ContextMetadata,
            line_number_base: LineNumberBase,
        ) -> str:
            line_desc = "1-based" if line_number_base == 1 else "0-based"
            return f"""## Context Loaded Successfully

**Context ID:** `{context_id}`
**Format:** {meta.format.value}
**Size:** {meta.size_chars:,} characters, {meta.size_lines:,} lines
**Estimated tokens:** ~{meta.size_tokens_estimate:,}
**Line numbers:** {line_desc}

**Preview (first 500 chars):**
```
{meta.sample_preview}
```

### Available Tools
- `peek_context`: View specific character or line ranges
- `search_context`: Search for patterns with regex
- `exec_python`: Execute Python code (context is in variable `ctx`)
- `get_variable`: Retrieve variables from the REPL
- `list_contexts`: List loaded contexts/sessions
- `diff_contexts`: Unified diff between two loaded contexts
- `save_session`: Save a session to disk (action tool)
- `load_session`: Load a session from disk (action tool)
- `load_file`: Load a workspace file into a context (action tool)
- `read_file`: Read a file from the workspace (action tool)
- `write_file`: Write a file in the workspace (action tool)
- `run_command`: Run a command in the workspace (action tool)
- `run_tests`: Run pytest tests and return structured failures (action tool)
- `think`: Structure your reasoning for complex sub-problems
- `get_status`: Check current session state
- `get_evidence`: Retrieve collected evidence/citations
- `chunk_context`: Split context into chunks with metadata
- `evaluate_progress`: Self-evaluate progress and convergence
- `summarize_so_far`: Summarize session progress
- `finalize`: Provide your final answer"""

        def _create_session(
            context: str,
            context_id: str,
            fmt: ContentFormat,
            line_number_base: LineNumberBase,
        ) -> ContextMetadata:
            meta = _analyze_text_context(context, fmt)
            repl = REPLEnvironment(
                context=context,
                context_var_name="ctx",
                config=self.sandbox_config,
                loop=asyncio.get_running_loop(),
            )
            repl.set_variable("line_number_base", line_number_base)
            self._sessions[context_id] = _Session(
                repl=repl,
                meta=meta,
                line_number_base=line_number_base,
            )
            return meta

        def _get_or_create_session(
            context_id: str,
            line_number_base: LineNumberBase | None = None,
        ) -> _Session:
            session = self._sessions.get(context_id)
            if session is not None:
                return session

            base = line_number_base if line_number_base is not None else DEFAULT_LINE_NUMBER_BASE
            meta = _analyze_text_context("", ContentFormat.TEXT)
            repl = REPLEnvironment(
                context="",
                context_var_name="ctx",
                config=self.sandbox_config,
                loop=asyncio.get_running_loop(),
            )
            repl.set_variable("line_number_base", base)
            session = _Session(repl=repl, meta=meta, line_number_base=base)
            self._sessions[context_id] = session
            return session

        @self.server.tool()
        async def load_context(
            context: str,
            context_id: str = "default",
            format: str = "auto",
            line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE,
        ) -> str:
            """Load context into an in-memory REPL session.

            The context is stored in a sandboxed Python environment as the variable `ctx`.
            You can then use other tools to explore and process this context.

            Args:
                context: The text/data to load
                context_id: Identifier for this context session (default: "default")
                format: Content format - "auto", "text", or "json" (default: "auto")
                line_number_base: Line number base for this context (0 or 1)

            Returns:
                Confirmation with context metadata
            """
            try:
                base = _validate_line_number_base(line_number_base)
            except ValueError as e:
                return f"Error: {e}"

            fmt = _detect_format(context) if format == "auto" else ContentFormat(format)
            meta = _create_session(context, context_id, fmt, base)
            return _format_context_loaded(context_id, meta, base)

        def _require_actions(confirm: bool) -> str | None:
            if not self.action_config.enabled:
                return "Actions are disabled. Start the server with `--enable-actions`."
            if self.action_config.require_confirmation and not confirm:
                return "Confirmation required. Re-run with confirm=true."
            return None

        def _record_action(session: _Session | None, note: str, snippet: str) -> None:
            if session is None:
                return
            evidence_before = len(session.evidence)
            session.evidence.append(
                _Evidence(
                    source="action",
                    line_range=None,
                    pattern=None,
                    note=note,
                    snippet=snippet[:200],
                )
            )
            session.information_gain.append(len(session.evidence) - evidence_before)

        async def _run_subprocess(
            argv: list[str],
            cwd: Path,
            timeout_seconds: float,
        ) -> dict[str, Any]:
            start = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            timed_out = False
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                stdout_b, stderr_b = await proc.communicate()

            duration_ms = (time.perf_counter() - start) * 1000.0
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            if len(stdout) > self.action_config.max_output_chars:
                stdout = stdout[: self.action_config.max_output_chars] + "\n... (truncated)"
            if len(stderr) > self.action_config.max_output_chars:
                stderr = stderr[: self.action_config.max_output_chars] + "\n... (truncated)"

            return {
                "argv": argv,
                "cwd": str(cwd),
                "exit_code": proc.returncode,
                "timed_out": timed_out,
                "duration_ms": duration_ms,
                "stdout": stdout,
                "stderr": stderr,
            }

        @self.server.tool()
        async def run_command(
            cmd: str,
            cwd: str | None = None,
            timeout_seconds: float | None = None,
            shell: bool = False,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
            context_id: str = "default",
        ) -> str | dict[str, Any]:
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            session = _get_or_create_session(context_id)
            session.iterations += 1

            workspace_root = self.action_config.workspace_root
            cwd_path = _scoped_path(workspace_root, cwd) if cwd else workspace_root
            timeout = timeout_seconds if timeout_seconds is not None else self.action_config.max_cmd_seconds

            if shell:
                argv = ["/bin/zsh", "-lc", cmd]
            else:
                argv = shlex.split(cmd)
                if not argv:
                    return _format_error("Empty command", output=output)

            payload = await _run_subprocess(argv=argv, cwd=cwd_path, timeout_seconds=timeout)
            if session is not None:
                session.repl._namespace["last_command_result"] = payload
            _record_action(session, note="run_command", snippet=(payload.get("stdout") or payload.get("stderr") or "")[:200])
            return _format_payload(payload, output=output)

        @self.server.tool()
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
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            base_override: LineNumberBase | None = None
            if line_number_base is not None:
                try:
                    base_override = _validate_line_number_base(line_number_base)
                except ValueError as e:
                    return _format_error(str(e), output=output)

            session = _get_or_create_session(context_id, line_number_base=base_override)
            session.iterations += 1
            try:
                base = _resolve_line_number_base(session, line_number_base)
            except ValueError as e:
                return _format_error(str(e), output=output)

            if base == 1 and start_line == 0:
                start_line = 1
            if start_line < base:
                return _format_error(f"start_line must be >= {base}", output=output)

            try:
                p = _scoped_path(self.action_config.workspace_root, path)
            except Exception as e:
                return _format_error(str(e), output=output)

            if not p.exists() or not p.is_file():
                return _format_error(f"File not found: {path}", output=output)

            data = p.read_bytes()
            if len(data) > self.action_config.max_read_bytes:
                return _format_error(
                    f"File too large to read (>{self.action_config.max_read_bytes} bytes): {path}",
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
            end_line = (start_idx + len(slice_lines) - 1 + base) if slice_lines else start_line

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
            if session is not None:
                session.repl._namespace["last_read_file_result"] = payload
            _record_action(session, note="read_file", snippet=f"{path} ({start_line}-{end_line})")
            return _format_payload(payload, output=output)

        @self.server.tool()
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
            err = _require_actions(confirm)
            if err:
                return f"Error: {err}"

            try:
                base = _validate_line_number_base(line_number_base)
            except ValueError as e:
                return f"Error: {e}"

            try:
                p = _scoped_path(self.action_config.workspace_root, path)
            except Exception as e:
                return f"Error: {e}"

            if not p.exists() or not p.is_file():
                return f"Error: File not found: {path}"

            data = p.read_bytes()
            if len(data) > self.action_config.max_read_bytes:
                return f"Error: File too large to read (>{self.action_config.max_read_bytes} bytes): {path}"

            text = data.decode("utf-8", errors="replace")
            fmt = _detect_format(text) if format == "auto" else ContentFormat(format)
            meta = _create_session(text, context_id, fmt, base)
            session = self._sessions[context_id]
            _record_action(session, note="load_file", snippet=str(p))
            return _format_context_loaded(context_id, meta, base)

        @self.server.tool()
        async def write_file(
            path: str,
            content: str,
            mode: Literal["overwrite", "append"] = "overwrite",
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
            context_id: str = "default",
        ) -> str | dict[str, Any]:
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            session = _get_or_create_session(context_id)
            session.iterations += 1

            try:
                p = _scoped_path(self.action_config.workspace_root, path)
            except Exception as e:
                return _format_error(str(e), output=output)

            payload_bytes = content.encode("utf-8", errors="replace")
            if len(payload_bytes) > self.action_config.max_write_bytes:
                return _format_error(
                    f"Content too large to write (>{self.action_config.max_write_bytes} bytes)",
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
            if session is not None:
                session.repl._namespace["last_write_file_result"] = payload
            _record_action(session, note="write_file", snippet=f"{path} ({len(payload_bytes)} bytes)")
            return _format_payload(payload, output=output)

        @self.server.tool()
        async def run_tests(
            runner: Literal["auto", "pytest"] = "auto",
            args: list[str] | None = None,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
            context_id: str = "default",
        ) -> str | dict[str, Any]:
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            session = _get_or_create_session(context_id)
            session.iterations += 1

            runner_resolved = "pytest" if runner == "auto" else runner
            if runner_resolved != "pytest":
                return _format_error(f"Unsupported test runner: {runner_resolved}", output=output)

            argv = [sys.executable, "-m", "pytest", "-vv", "--tb=short", "--maxfail=20"]
            if args:
                argv.extend(args)

            proc_payload = await _run_subprocess(
                argv=argv,
                cwd=self.action_config.workspace_root,
                timeout_seconds=self.action_config.max_cmd_seconds,
            )
            raw_output = (proc_payload.get("stdout") or "") + ("\n" + proc_payload.get("stderr") if proc_payload.get("stderr") else "")

            passed = 0
            failed = 0
            errors = 0
            duration_ms = float(proc_payload.get("duration_ms") or 0.0)
            exit_code = int(proc_payload.get("exit_code") or 0)

            m_passed = re.search(r"(\\d+)\\s+passed", raw_output)
            if m_passed:
                passed = int(m_passed.group(1))
            m_failed = re.search(r"(\\d+)\\s+failed", raw_output)
            if m_failed:
                failed = int(m_failed.group(1))
            m_errors = re.search(r"(\\d+)\\s+errors?", raw_output)
            if m_errors:
                errors = int(m_errors.group(1))

            failures: list[dict[str, Any]] = []
            section_re = re.compile(r"^_{3,}\\s+(?P<name>.+?)\\s+_{3,}\\s*$", re.MULTILINE)
            matches = list(section_re.finditer(raw_output))
            for i, sm in enumerate(matches):
                start = sm.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_output)
                block = raw_output[start:end].strip()
                file = ""
                line = 0
                file_line = re.search(r"^(?P<file>.+?\\.py):(?P<line>\\d+):", block, re.MULTILINE)
                if file_line:
                    file = file_line.group("file")
                    try:
                        line = int(file_line.group("line"))
                    except Exception:
                        line = 0
                msg = ""
                err_line = re.search(r"^E\\s+(.+)$", block, re.MULTILINE)
                if err_line:
                    msg = err_line.group(1).strip()

                failures.append(
                    {
                        "file": file,
                        "line": line,
                        "test_name": sm.group("name").strip(),
                        "message": msg,
                        "traceback": block,
                    }
                )

            if exit_code != 0 and failed == 0 and errors == 0:
                errors = 1

            status = "passed"
            if exit_code != 0:
                status = "failed" if failed > 0 else "error"

            result: dict[str, Any] = {
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "failures": failures,
                "status": status,
                "duration_ms": duration_ms,
                "exit_code": exit_code,
                "raw_output": raw_output,
                "command": proc_payload,
            }

            if session is not None:
                session.repl._namespace["last_test_result"] = result

            summary_snippet = (
                f"status={status} passed={passed} failed={failed} errors={errors} "
                f"failures={len(failures)} exit_code={exit_code}"
            )
            _record_action(session, note="run_tests", snippet=summary_snippet)
            for f in failures[:10]:
                _record_action(session, note="test_failure", snippet=(f.get("message") or f.get("test_name") or "")[:200])

            return _format_payload(result, output=output)

        @self.server.tool()
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

        @self.server.tool()
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

            _record_action(sa, note="diff_contexts", snippet=f"{a} vs {b}")
            _record_action(sb, note="diff_contexts", snippet=f"{a} vs {b}")

            if output == "text":
                return diff_text
            return f"```diff\n{diff_text}\n```"

        @self.server.tool()
        async def save_session(
            session_id: str = "default",
            context_id: str | None = None,
            path: str = "aleph_session.json",
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            target_id = context_id or session_id
            if target_id not in self._sessions:
                return _format_error(f"No context loaded with ID '{target_id}'. Use load_context first.", output=output)

            session = self._sessions[target_id]
            session.iterations += 1

            ctx_val = session.repl.get_variable("ctx")
            if not isinstance(ctx_val, str):
                return _format_error("save_session currently supports only text contexts", output=output)

            payload: dict[str, Any] = {
                "schema": "aleph.session.v1",
                "session_id": target_id,
                "context_id": target_id,
                "created_at": session.created_at.isoformat(),
                "iterations": session.iterations,
                "line_number_base": session.line_number_base,
                "meta": {
                    "format": session.meta.format.value,
                    "size_bytes": session.meta.size_bytes,
                    "size_chars": session.meta.size_chars,
                    "size_lines": session.meta.size_lines,
                    "size_tokens_estimate": session.meta.size_tokens_estimate,
                    "structure_hint": session.meta.structure_hint,
                    "sample_preview": session.meta.sample_preview,
                },
                "ctx": ctx_val,
                "think_history": list(session.think_history),
                "confidence_history": list(session.confidence_history),
                "information_gain": list(session.information_gain),
                "chunks": session.chunks,
                "evidence": [
                    {
                        "source": ev.source,
                        "line_range": list(ev.line_range) if ev.line_range else None,
                        "pattern": ev.pattern,
                        "snippet": ev.snippet,
                        "note": ev.note,
                        "timestamp": ev.timestamp.isoformat(),
                    }
                    for ev in session.evidence
                ],
            }

            out_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8", errors="replace")
            if len(out_bytes) > self.action_config.max_write_bytes:
                return _format_error(
                    f"Session file too large to write (>{self.action_config.max_write_bytes} bytes)",
                    output=output,
                )

            try:
                p = _scoped_path(self.action_config.workspace_root, path)
            except Exception as e:
                return _format_error(str(e), output=output)

            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.write(out_bytes)

            _record_action(session, note="save_session", snippet=str(p))
            return _format_payload({"path": str(p), "bytes_written": len(out_bytes)}, output=output)

        @self.server.tool()
        async def load_session(
            path: str,
            session_id: str | None = None,
            context_id: str | None = None,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            try:
                p = _scoped_path(self.action_config.workspace_root, path)
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

            ctx = obj.get("ctx")
            if not isinstance(ctx, str):
                return _format_error("Invalid session file: ctx must be a string", output=output)

            file_session_id = obj.get("context_id") or obj.get("session_id")
            resolved_id = context_id or session_id or (str(file_session_id) if file_session_id else "default")

            meta_obj = obj.get("meta")
            if not isinstance(meta_obj, dict):
                meta_obj = {}

            try:
                fmt = ContentFormat(str(meta_obj.get("format") or "text"))
            except Exception:
                fmt = ContentFormat.TEXT

            meta = ContextMetadata(
                format=fmt,
                size_bytes=int(meta_obj.get("size_bytes") or len(ctx.encode("utf-8", errors="ignore"))),
                size_chars=int(meta_obj.get("size_chars") or len(ctx)),
                size_lines=int(meta_obj.get("size_lines") or (ctx.count("\n") + 1)),
                size_tokens_estimate=int(meta_obj.get("size_tokens_estimate") or (len(ctx) // 4)),
                structure_hint=meta_obj.get("structure_hint"),
                sample_preview=str(meta_obj.get("sample_preview") or ctx[:500]),
            )

            repl = REPLEnvironment(
                context=ctx,
                context_var_name="ctx",
                config=self.sandbox_config,
                loop=asyncio.get_running_loop(),
            )
            line_number_base = obj.get("line_number_base")
            if line_number_base is None:
                line_number_base = 0
            try:
                base = _validate_line_number_base(int(line_number_base))
            except Exception:
                base = DEFAULT_LINE_NUMBER_BASE
            repl.set_variable("line_number_base", base)

            created_at = datetime.now()
            created_at_str = obj.get("created_at")
            if isinstance(created_at_str, str):
                try:
                    created_at = datetime.fromisoformat(created_at_str)
                except Exception:
                    created_at = datetime.now()

            session = _Session(
                repl=repl,
                meta=meta,
                line_number_base=base,
                created_at=created_at,
                iterations=int(obj.get("iterations") or 0),
                think_history=list(obj.get("think_history") or []),
                confidence_history=list(obj.get("confidence_history") or []),
                information_gain=list(obj.get("information_gain") or []),
                chunks=obj.get("chunks"),
            )

            ev_list = obj.get("evidence")
            if isinstance(ev_list, list):
                for ev in ev_list:
                    if not isinstance(ev, dict):
                        continue
                    ts = datetime.now()
                    ts_s = ev.get("timestamp")
                    if isinstance(ts_s, str):
                        try:
                            ts = datetime.fromisoformat(ts_s)
                        except Exception:
                            ts = datetime.now()
                    source = ev.get("source")
                    if source not in {"search", "peek", "exec", "manual", "action"}:
                        source = "manual"
                    lr = ev.get("line_range")
                    line_range: tuple[int, int] | None = None
                    if isinstance(lr, list) and len(lr) == 2 and all(isinstance(x, int) for x in lr):
                        line_range = (int(lr[0]), int(lr[1]))
                    session.evidence.append(
                        _Evidence(
                            source=source,
                            line_range=line_range,
                            pattern=ev.get("pattern"),
                            snippet=str(ev.get("snippet") or ""),
                            note=ev.get("note"),
                            timestamp=ts,
                        )
                    )

            self._sessions[resolved_id] = session
            _record_action(session, note="load_session", snippet=str(p))
            return _format_payload(
                {
                    "context_id": resolved_id,
                    "session_id": resolved_id,
                    "line_number_base": base,
                    "loaded_from": str(p),
                },
                output=output,
            )

        @self.server.tool()
        async def peek_context(
            start: int = 0,
            end: int | None = None,
            context_id: str = "default",
            unit: Literal["chars", "lines"] = "chars",
            record_evidence: bool = False,
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
            if context_id not in self._sessions:
                return _format_error(
                    f"No context loaded with ID '{context_id}'. Use load_context first.",
                    output=output,
                )

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

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

        @self.server.tool()
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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

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

        @self.server.tool()
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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

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

        @self.server.tool()
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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            repl = self._sessions[context_id].repl
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

        @self.server.tool()
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

        @self.server.tool()
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

        @self.server.tool()
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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]
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

        @self.server.tool()
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

            return "\n".join(parts)

        # =====================================================================
        # Sub-query tool (RLM-style recursive reasoning)
        # =====================================================================

        @self.server.tool()
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
            1. API - if MIMO_API_KEY or OPENAI_API_KEY is set (most reliable)
            2. claude CLI - if installed
            3. codex CLI - if installed
            4. aider CLI - if installed

            Configure via environment:
            - ALEPH_SUB_QUERY_BACKEND: Force specific backend ("api", "claude", "codex", "aider")
            - MIMO_API_KEY or OPENAI_API_KEY: API credentials
            - OPENAI_BASE_URL: API endpoint (default: Mimo Flash V2)
            - ALEPH_SUB_QUERY_MODEL: Model name (default: mimo-v2-flash)

            Args:
                prompt: The question/task for the sub-agent
                context_slice: Optional context to include (e.g., a chunk from ctx)
                context_id: Session to record evidence in
                backend: "auto", "claude", "codex", "aider", or "api"

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
                    success, output = await run_cli_sub_query(
                        prompt=prompt,
                        context_slice=context_slice,
                        backend=resolved_backend,  # type: ignore
                        timeout=self.sub_query_config.cli_timeout_seconds,
                        cwd=self.action_config.workspace_root if self.action_config.enabled else None,
                        max_output_chars=self.sub_query_config.cli_max_output_chars,
                    )
                else:
                    success, output = await run_api_sub_query(
                        prompt=prompt,
                        context_slice=context_slice,
                        model=self.sub_query_config.api_model,
                        api_key_env=self.sub_query_config.api_key_env,
                        api_base_url_env=self.sub_query_config.api_base_url_env,
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

        @self.server.tool()
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
            err = _require_actions(confirm)
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
                ok, res = await self._ensure_remote_server(server_id)
                if not ok:
                    return _format_error(str(res), output=output)
                handle = res  # type: ignore[assignment]
                try:
                    r = await handle.session.list_tools()  # type: ignore[union-attr]
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

        @self.server.tool()
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

        @self.server.tool()
        async def list_remote_tools(
            server_id: str,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """List tools available on a remote MCP server."""
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            ok, res = await self._ensure_remote_server(server_id)
            if not ok:
                return _format_error(str(res), output=output)
            ok2, tools = await self._remote_list_tools(server_id)
            if not ok2:
                return _format_error(str(tools), output=output)
            return _format_payload(tools, output=output)

        @self.server.tool()
        async def call_remote_tool(
            server_id: str,
            tool: str,
            arguments: dict[str, Any] | None = None,
            timeout_seconds: float | None = 30.0,
            recipe_id: str | None = None,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Call a tool on a remote MCP server and record it in the run trace.

            Args:
                server_id: Registered remote server ID
                tool: Tool name
                arguments: Tool arguments object
                timeout_seconds: Tool call timeout (best-effort)
                recipe_id: If provided, attaches call to a recipe trace/evidence
                confirm: Required if actions are enabled
                output: Output format
            """
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            ok, res = await self._ensure_remote_server(server_id)
            if not ok:
                return _format_error(str(res), output=output)
            ok2, result_jsonable = await self._remote_call_tool(
                server_id=server_id,
                tool=tool,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                recipe_id=recipe_id,
            )
            if not ok2:
                return _format_error(str(result_jsonable), output=output)

            if output == "object":
                return result_jsonable
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

        @self.server.tool()
        async def close_remote_server(
            server_id: str,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Close a remote MCP server connection (terminates subprocess)."""
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            ok, msg = await self._close_remote_server(server_id)
            if output == "object":
                return {"ok": ok, "message": msg}
            if output == "json":
                return json.dumps({"ok": ok, "message": msg}, indent=2)
            return msg

        @self.server.tool()
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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

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

        @self.server.tool()
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
                f"**Current Understanding:**",
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

        @self.server.tool()
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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]

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

        # =====================================================================
        # Recipe/Alephfile Tools (v0.5)
        # =====================================================================

        @self.server.tool()
        async def load_recipe(
            path: str,
            recipe_id: str = "default",
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Load an Alephfile recipe for execution.

            An Alephfile defines a reproducible analysis run with datasets,
            query, tool config, and budget constraints.

            Args:
                path: Path to the Alephfile (JSON or YAML)
                recipe_id: Identifier for this recipe (default: "default")
                confirm: Required if actions are enabled
                output: Output format

            Returns:
                Recipe summary with datasets and configuration
            """
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            try:
                p = _scoped_path(self.action_config.workspace_root, path)
            except Exception as e:
                return _format_error(str(e), output=output)

            try:
                config = load_alephfile(p)
            except Exception as e:
                return _format_error(f"Error loading Alephfile: {e}", output=output)

            runner = RecipeRunner(config)
            runner.start()

            # Load datasets and compute baseline
            try:
                loaded = runner.load_datasets()
            except Exception as e:
                return _format_error(f"Error loading datasets: {e}", output=output)

            self._recipes[recipe_id] = runner

            # Also load datasets into sessions for exploration
            for ds_id, content in loaded.items():
                ctx_id = f"{recipe_id}:{ds_id}"
                fmt = _detect_format(content)
                meta = _analyze_text_context(content, fmt)
                repl = REPLEnvironment(
                    context=content,
                    context_var_name="ctx",
                    config=self.sandbox_config,
                    loop=asyncio.get_running_loop(),
                )
                repl.set_variable("line_number_base", DEFAULT_LINE_NUMBER_BASE)
                self._sessions[ctx_id] = _Session(
                    repl=repl,
                    meta=meta,
                    line_number_base=DEFAULT_LINE_NUMBER_BASE,
                )

            if output in {"json", "object"}:
                return _format_payload(
                    {
                        "recipe_id": recipe_id,
                        "query": config.query,
                        "datasets": [d.to_dict() for d in config.datasets],
                        "baseline_tokens": runner.metrics.tokens_baseline,
                        "model": config.model,
                        "max_iterations": config.max_iterations,
                    },
                    output=output,
                )

            parts = [
                "## Recipe Loaded",
                "",
                f"**Recipe ID:** `{recipe_id}`",
                f"**Query:** {config.query}",
                "",
                "### Datasets",
            ]
            for ds in config.datasets:
                parts.append(f"- `{ds.id}`: {ds.size_bytes:,} bytes, ~{ds.size_tokens_estimate:,} tokens")
                if ds.content_hash:
                    parts.append(f"  - Hash: `{ds.content_hash[:32]}...`")

            parts.extend([
                "",
                "### Budget",
                f"- Max iterations: {config.max_iterations}",
                f"- Max tokens: {config.max_tokens or 'unlimited'}",
                f"- Timeout: {config.timeout_seconds}s",
                "",
                "### Baseline Estimate",
                f"- Context-stuffing approach would use ~{runner.metrics.tokens_baseline:,} tokens",
                "",
                "*Use `get_metrics(recipe_id)` during execution to track efficiency.*",
            ])
            return "\n".join(parts)

        @self.server.tool()
        async def get_metrics(
            recipe_id: str = "default",
            context_id: str | None = None,
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Get token efficiency metrics for a recipe or session.

            Shows tokens used vs baseline (context-stuffing approach) and
            computes efficiency ratio = tokens_saved / tokens_baseline.

            Args:
                recipe_id: Recipe identifier
                context_id: Optional specific context to get metrics for
                output: Output format

            Returns:
                Metrics including tokens_used, tokens_baseline, tokens_saved, efficiency_ratio
            """
            if recipe_id not in self._recipes:
                # Fall back to session-based metrics
                cid = context_id or "default"
                if cid not in self._sessions:
                    return _format_error(f"No recipe '{recipe_id}' or session '{cid}' found", output=output)

                session = self._sessions[cid]
                # Estimate baseline from session metadata
                baseline = session.meta.size_tokens_estimate * 3 + 500 * 3
                # Estimate tokens used from iterations (rough: 500 per iteration)
                used = session.iterations * 500
                saved = max(0, baseline - used)
                ratio = saved / baseline if baseline > 0 else 0.0

                metrics = {
                    "context_id": cid,
                    "tokens_used": used,
                    "tokens_baseline": baseline,
                    "tokens_saved": saved,
                    "efficiency_ratio": round(ratio, 4),
                    "iterations": session.iterations,
                    "evidence_count": len(session.evidence),
                }
            else:
                runner = self._recipes[recipe_id]
                runner.metrics.compute_efficiency()
                metrics = {
                    "recipe_id": recipe_id,
                    **runner.metrics.to_dict(),
                }

            if output in {"json", "object"}:
                return _format_payload(metrics, output=output)

            parts = [
                "## Token Efficiency Metrics",
                "",
                f"**Tokens Used:** {metrics['tokens_used']:,}",
                f"**Tokens Baseline:** {metrics['tokens_baseline']:,}",
                f"**Tokens Saved:** {metrics['tokens_saved']:,}",
                f"**Efficiency Ratio:** {metrics['efficiency_ratio']:.1%}",
                "",
                f"*Iterations: {metrics['iterations']} | Evidence: {metrics.get('evidence_count', 0)}*",
            ]

            if metrics['efficiency_ratio'] > 0.5:
                parts.append("")
                parts.append(f"🎯 **Aleph saved {metrics['efficiency_ratio']:.0%} of tokens vs context-stuffing!**")

            return "\n".join(parts)

        @self.server.tool()
        async def finalize_recipe(
            recipe_id: str = "default",
            answer: str = "",
            success: bool = True,
            context_id: str | None = None,
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Finalize a recipe run and generate the result bundle.

            Collects all evidence, computes final metrics, and produces
            a reproducible result that can be exported.

            Args:
                recipe_id: Recipe identifier
                answer: Final answer from the analysis
                success: Whether the analysis succeeded
                context_id: Optional context to pull evidence from
                output: Output format

            Returns:
                Final result summary with metrics and evidence count
            """
            if recipe_id not in self._recipes:
                return _format_error(f"No recipe '{recipe_id}' found. Use load_recipe first.", output=output)

            runner = self._recipes[recipe_id]

            # Collect evidence from associated sessions
            for ds in runner.config.datasets:
                ctx_id = f"{recipe_id}:{ds.id}"
                if ctx_id in self._sessions:
                    session = self._sessions[ctx_id]
                    for ev in session.evidence:
                        runner.add_evidence(
                            source=ev.source,
                            snippet=ev.snippet,
                            line_range=ev.line_range,
                            pattern=ev.pattern,
                            note=ev.note,
                            dataset_id=ds.id,
                        )

            # Also collect from specified context_id
            if context_id and context_id in self._sessions:
                session = self._sessions[context_id]
                for ev in session.evidence:
                    runner.add_evidence(
                        source=ev.source,
                        snippet=ev.snippet,
                        line_range=ev.line_range,
                        pattern=ev.pattern,
                        note=ev.note,
                    )

            result = runner.finalize(answer, success)
            self._recipe_results[recipe_id] = result

            if output in {"json", "object"}:
                return _format_payload(result.to_dict(), output=output)

            parts = [
                "## Recipe Result",
                "",
                f"**Recipe ID:** `{recipe_id}`",
                f"**Success:** {result.success}",
                "",
                "### Answer",
                result.answer,
                "",
                "### Metrics",
                f"- Tokens used: {result.metrics.tokens_used:,}",
                f"- Tokens saved: {result.metrics.tokens_saved:,}",
                f"- Efficiency: {result.metrics.efficiency_ratio:.1%}",
                f"- Iterations: {result.metrics.iterations}",
                f"- Wall time: {result.metrics.wall_time_seconds:.2f}s",
                "",
                "### Evidence",
                f"- Total items: {len(result.evidence_bundle.evidence)}",
                "",
                f"*Use `export_result('{recipe_id}')` to save the full result bundle.*",
            ]
            return "\n".join(parts)

        @self.server.tool()
        async def export_result(
            recipe_id: str = "default",
            path: str = "aleph_result.json",
            include_trace: bool = True,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """Export a recipe result to a file.

            Produces a JSON file with the complete reproducible result:
            recipe config, answer, metrics, evidence bundle, and execution trace.

            Args:
                recipe_id: Recipe identifier
                path: Output file path
                include_trace: Whether to include the execution trace
                confirm: Required if actions are enabled

            Returns:
                Confirmation with file path and size
            """
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            if recipe_id not in self._recipe_results:
                return _format_error(
                    f"No finalized result for recipe '{recipe_id}'. Use finalize_recipe first.",
                    output=output,
                )

            result = self._recipe_results[recipe_id]

            try:
                p = _scoped_path(self.action_config.workspace_root, path)
            except Exception as e:
                return _format_error(str(e), output=output)

            data = result.to_dict()
            if not include_trace:
                data["trace"] = []

            content = json.dumps(data, indent=2, ensure_ascii=False)
            content_bytes = content.encode("utf-8")

            if len(content_bytes) > self.action_config.max_write_bytes:
                return _format_error(
                    f"Result too large to export (>{self.action_config.max_write_bytes} bytes)",
                    output=output,
                )

            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.write(content_bytes)

            return _format_payload({
                "path": str(p),
                "bytes_written": len(content_bytes),
                "recipe_id": recipe_id,
                "evidence_count": len(result.evidence_bundle.evidence),
            }, output=output)

        @self.server.tool()
        async def sign_evidence(
            recipe_id: str = "default",
            signer_id: str = "local",
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Sign an evidence bundle for verification.

            Creates a cryptographic hash of the evidence bundle that can be
            verified later to ensure the evidence hasn't been tampered with.

            Note: This is a content hash signature, not a PKI signature.
            For full PKI signing, use an external signing service.

            Args:
                recipe_id: Recipe identifier
                signer_id: Identifier for the signer
                output: Output format

            Returns:
                Signed bundle summary with hash
            """
            if recipe_id not in self._recipe_results:
                return _format_error(
                    f"No finalized result for recipe '{recipe_id}'. Use finalize_recipe first.",
                    output=output,
                )

            result = self._recipe_results[recipe_id]
            bundle = result.evidence_bundle

            # Compute content hash as signature
            bundle.signature = bundle.compute_hash()
            bundle.signed_at = datetime.now().isoformat()
            bundle.signed_by = signer_id

            if output in {"json", "object"}:
                return _format_payload(
                    {
                        "recipe_id": recipe_id,
                        "signature": bundle.signature,
                        "signed_at": bundle.signed_at,
                        "signed_by": bundle.signed_by,
                        "evidence_count": len(bundle.evidence),
                    },
                    output=output,
                )

            parts = [
                "## Evidence Bundle Signed",
                "",
                f"**Recipe ID:** `{recipe_id}`",
                f"**Signature:** `{bundle.signature}`",
                f"**Signed At:** {bundle.signed_at}",
                f"**Signed By:** {bundle.signed_by}",
                f"**Evidence Items:** {len(bundle.evidence)}",
                "",
                "*This hash can be used to verify the evidence bundle hasn't been modified.*",
            ]
            return "\n".join(parts)

        @self.server.tool()
        async def list_recipes(
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """List all loaded recipes and their status.

            Returns:
                List of recipes with their current state
            """
            items = []
            for rid, runner in self._recipes.items():
                finalized = rid in self._recipe_results
                items.append({
                    "recipe_id": rid,
                    "query": runner.config.query[:100],
                    "datasets": len(runner.config.datasets),
                    "iterations": runner.metrics.iterations,
                    "evidence_count": runner.metrics.evidence_count,
                    "finalized": finalized,
                })

            if output in {"json", "object"}:
                return _format_payload({"count": len(items), "items": items}, output=output)

            if not items:
                return "No recipes loaded. Use `load_recipe` to load an Alephfile."

            parts = ["## Loaded Recipes", ""]
            for item in items:
                status = "✓ finalized" if item["finalized"] else "⏳ in progress"
                parts.append(f"- `{item['recipe_id']}`: {status}")
                parts.append(f"  - Query: {item['query']}")
                parts.append(f"  - Datasets: {item['datasets']} | Iterations: {item['iterations']} | Evidence: {item['evidence_count']}")

            return "\n".join(parts)

    async def run(self, transport: str = "stdio") -> None:
        """Run the MCP server."""
        if transport != "stdio":
            raise ValueError("Only stdio transport is supported")

        await self.server.run_stdio_async()


def main() -> None:
    """CLI entry point: `aleph-mcp-local` or `python -m aleph.mcp.local_server`"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Aleph as an API-free MCP server for local AI reasoning"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Code execution timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-output",
        type=int,
        default=10000,
        help="Maximum output characters (default: 10000)",
    )
    parser.add_argument(
        "--enable-actions",
        action="store_true",
        help="Enable action tools (run_command/read_file/write_file/run_tests)",
    )
    parser.add_argument(
        "--workspace-root",
        type=str,
        default=None,
        help="Workspace root for action tools (default: auto-detect git root or cwd)",
    )
    parser.add_argument(
        "--require-confirmation",
        action="store_true",
        help="Require confirm=true for action tools",
    )

    args = parser.parse_args()

    config = SandboxConfig(
        timeout_seconds=args.timeout,
        max_output_chars=args.max_output,
    )

    action_cfg = ActionConfig(
        enabled=bool(args.enable_actions),
        workspace_root=Path(args.workspace_root).resolve() if args.workspace_root else _detect_workspace_root(),
        require_confirmation=bool(args.require_confirmation),
    )

    server = AlephMCPServerLocal(sandbox_config=config, action_config=action_cfg)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()

"""Aleph MCP server for use with Claude Desktop, Cursor, Windsurf, etc.

This server exposes Aleph's context exploration tools and optional action tools.

Tools:
- load_context: Load text/data into sandboxed REPL
- peek_context: View character/line ranges
- search_context: Regex search with context
- semantic_search: Meaning-based search over the context
- exec_python: Execute Python code in sandbox
- exec_javascript: Execute JavaScript code in persistent Node.js runtime
- exec_typescript: Execute TypeScript code in persistent Node.js runtime
- get_variable: Retrieve variables from REPL
- think: Structure a reasoning sub-step (returns prompt for YOU to reason about)
- tasks: Lightweight task tracking per context
- get_status: Show current session state
- get_evidence: Retrieve collected evidence/citations
- finalize: Mark task complete with answer
- chunk_context: Split context into chunks with metadata for navigation
- evaluate_progress: Self-evaluate progress with convergence tracking
- summarize_so_far: Compress reasoning history to manage context window
- validate_recipe: Validate recipe pipelines before execution
- estimate_recipe: Static estimate of recipe cost/shape
- run_recipe: Execute declarative recipe pipelines
- compile_recipe: Compile Recipe DSL code into recipe payload
- run_recipe_code: Compile + execute Recipe DSL code
- run_command: Run a shell command (action tool)
- read_file: Read file contents (action tool)
- write_file: Write file contents (action tool)
- load_file: Load files into context (action tool)
- run_tests: Run tests (action tool)
- rg_search: Fast repo search via ripgrep (action tool)

RLM recursion is available inside `exec_python`, `exec_javascript`, and
`exec_typescript` via REPL helpers (`sub_query`, `sub_query_batch`,
`sub_query_map`, `sub_aleph`). The JS/TS variants are async and intended to be
used with top-level `await`.

Usage:
    aleph
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import inspect
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

from ..compat import normalize_output_feedback
from ..config import AlephConfig
from ..core import Aleph  # noqa: F401 - compatibility for external patching/imports
from ..repl.node_runtime import NodeREPLEnvironment  # noqa: F401 - re-export
from ..repl.sandbox import REPLEnvironment, SandboxConfig
from ..types import (
    AlephResponse,
    ContentFormat,
    ContextMetadata,
    ExecutionResult,
)
from ..sub_query import SubQueryConfig
from ..sub_query.cli_backend import CLI_BACKENDS
from .actions import (
    ActionConfig,
    _parse_rg_vimgrep as _parse_rg_vimgrep_impl,
    _python_rg_search as _python_rg_search_impl,
    record_action as _record_action_impl,
    require_actions as _require_actions_impl,
    run_subprocess as _run_subprocess_impl,
)
from .action_tools import register_action_tools as _register_action_tools_module
from .admin_tools import register_admin_tools
from .context_tools import register_context_tools as _register_context_tools_module
from .env_utils import DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS, _get_env_int
from .formatting import (
    DEFAULT_TOOL_RESPONSE_MAX_CHARS,
    DEFAULT_TOOL_TRUNCATION_SUFFIX,
    _format_context_loaded as _format_context_loaded_impl,
    _format_error,
    _format_execution_result as _format_execution_result_impl,
    _format_payload,
    _format_variable_value as _format_variable_value_impl,
    _limit_json_items as _limit_json_items_impl,
    _to_jsonable,
    _truncate_tool_text as _truncate_tool_text_impl,
)
from .io_utils import _detect_format, _load_text_from_path  # noqa: F401
from .node_bridge import (
    close_node_repl as _close_node_repl_impl,
    configure_node_repl as _configure_node_repl_impl,
    get_or_create_node_repl as _get_or_create_node_repl_impl,
    sync_session_from_node_repl as _sync_session_from_node_repl_impl,
)
from .query_tools import register_query_tools as _register_query_tools_module
from .repl_injection import (
    configure_session as _configure_session_impl,
    inject_repl_config_helpers as _inject_repl_config_helpers_impl,
    inject_repl_sub_aleph as _inject_repl_sub_aleph_impl,
    inject_repl_sub_query as _inject_repl_sub_query_impl,
)
from .recipe_runtime import (
    compile_recipe_code as _compile_recipe_code_impl,
    execute_recipe as _execute_recipe_impl,
    recipe_context_slice as _recipe_context_slice_impl,
    recipe_preview as _recipe_preview_impl,
)
from .reasoning_tools import (
    register_reasoning_tools as _register_reasoning_tools_module,
)
from .remote_servers import (
    _RemoteServerHandle,
    close_remote_server,
    ensure_remote_server,
    remote_call_tool,
    remote_list_tools,
    remote_tool_allowed,
    reset_remote_server_handle,
)
from .server_bootstrap import (
    apply_server_env_overrides,
    build_runtime_configs,
    build_server_argument_parser,
)
from .sub_query_runtime import (
    apply_sub_query_runtime_config,
    get_sub_query_config_snapshot,
)
from .sub_query_orchestration import (
    build_sub_aleph_cli_prompt as _build_sub_aleph_cli_prompt_impl,
    ensure_internal_codex_mcp_server as _ensure_internal_codex_mcp_server_impl,
    ensure_streamable_http_server as _ensure_streamable_http_server_impl,
    extract_final_answer as _extract_final_answer_impl,
    format_streamable_http_url as _format_streamable_http_url_impl,
    normalize_streamable_http_path as _normalize_streamable_http_path_impl,
    run_internal_codex_mcp_query as _run_internal_codex_mcp_query_impl,
    run_streamable_http_server as _run_streamable_http_server_impl,
    run_sub_aleph as _run_sub_aleph_impl,
    run_sub_query as _run_sub_query_impl,
    wait_for_streamable_http_ready as _wait_for_streamable_http_ready_impl,
)
from . import workspace as _workspace
from .workspace import (
    DEFAULT_WORKSPACE_MODE,
    LineNumberBase,
    _detect_workspace_root,
    _scoped_path,
    _validate_line_number_base,
    roots_to_workspace_root,
)
from .workspace_tools import register_workspace_tools
from .session import (
    MEMORY_PACK_RELATIVE_PATH,
    _Evidence,  # noqa: F401 - compatibility for external imports
    _Session,
    _resolve_session_payload_id,  # noqa: F401
    build_memory_pack_payload as _build_memory_pack_payload_impl,
    create_session as _create_session_impl,
    get_or_create_session as _get_or_create_session_impl,
    load_memory_pack_payload as _load_memory_pack_payload,
    replace_session_context as _replace_session_context_impl,
    restore_session_state as _restore_session_state_impl,
    snapshot_session_state as _snapshot_session_state_impl,
)

__all__ = ["AlephMCPServerLocal", "main", "mcp"]

mcp: Any


_find_git_root = _workspace._find_git_root
_nearest_existing_parent = _workspace._nearest_existing_parent


ToolDocsMode = Literal["concise", "full"]
DEFAULT_TOOL_DOCS_MODE: ToolDocsMode = "concise"
ContextPolicy = Literal["trusted", "isolated"]
DEFAULT_CONTEXT_POLICY: ContextPolicy = "trusted"
ActionPolicy = Literal["read-write", "read-only"]
DEFAULT_ACTION_POLICY: ActionPolicy = "read-write"
_TOOL_TRUNCATION_SUFFIX = DEFAULT_TOOL_TRUNCATION_SUFFIX


def _normalize_context_policy(
    value: str | None,
    default: str = DEFAULT_CONTEXT_POLICY,
) -> ContextPolicy:
    if value is None:
        return cast(ContextPolicy, default)
    normalized = value.strip().lower()
    if normalized in {"trusted", "isolated"}:
        return cast(ContextPolicy, normalized)
    if normalized in {"strict", "untrusted", "shared"}:
        return "isolated"
    return cast(ContextPolicy, default)


def _normalize_action_policy(
    value: str | None,
    default: str = DEFAULT_ACTION_POLICY,
) -> ActionPolicy:
    if value is None:
        return cast(ActionPolicy, default)
    normalized = value.strip().lower()
    if normalized in {"read-write", "workspace-write", "write"}:
        return "read-write"
    if normalized in {"read-only", "readonly", "safe"}:
        return "read-only"
    return cast(ActionPolicy, default)


_ANALYZE_CACHE_MAX = 64
_ANALYZE_CACHE: OrderedDict[tuple[int, int, ContentFormat], ContextMetadata] = (
    OrderedDict()
)


def _analyze_text_context(text: str, fmt: ContentFormat) -> ContextMetadata:
    """Analyze text and return metadata."""
    key = (hash(text), len(text), fmt)
    cached = _ANALYZE_CACHE.get(key)
    if cached is not None:
        _ANALYZE_CACHE.move_to_end(key)
        return cached

    meta = ContextMetadata(
        format=fmt,
        size_bytes=len(text.encode("utf-8", errors="ignore")),
        size_chars=len(text),
        size_lines=text.count("\n") + 1,
        size_tokens_estimate=len(text) // 4,
        structure_hint=None,
        sample_preview=text[:500],
    )
    _ANALYZE_CACHE[key] = meta
    if len(_ANALYZE_CACHE) > _ANALYZE_CACHE_MAX:
        _ANALYZE_CACHE.popitem(last=False)
    return meta


def _extract_final_answer(text: str) -> tuple[str, bool]:
    return _extract_final_answer_impl(text)


def _build_sub_aleph_cli_prompt(
    *,
    query: str,
    context_slice: str,
    context_format: ContentFormat,
    cfg: AlephConfig,
) -> str:
    return _build_sub_aleph_cli_prompt_impl(
        query=query,
        context_slice=context_slice,
        context_format=context_format,
        cfg=cfg,
        analyze_text_context=_analyze_text_context,
    )


def _to_internal_line_index(index: int | None, base: int) -> int | None:
    """Convert external line indices (line_number_base) to internal 0-based indices."""

    if index is None or index < 0:
        return index
    resolved_base = _validate_line_number_base(base)
    if resolved_base == 0:
        return index
    if index == 0:
        # Backward-compatible handling for older callers that still pass 0-based values.
        return 0
    return index - 1


def _get_repl_helper(repl: REPLEnvironment, name: str) -> object | None:
    """Return a helper callable, preferring stable helper references."""

    get_helper = getattr(repl, "get_helper", None)
    if callable(get_helper):
        helper = get_helper(name)
        if helper is not None:
            return cast(object, helper)
    return cast(object | None, repl.get_variable(name))


class AlephMCPServerLocal:
    """MCP server for local AI reasoning.

    This server provides context exploration tools that work with any
    MCP-compatible AI host (Claude Desktop, Cursor, Windsurf, etc.).
    """

    def __init__(
        self,
        sandbox_config: SandboxConfig | None = None,
        action_config: ActionConfig | None = None,
        sub_query_config: SubQueryConfig | None = None,
        tool_docs_mode: ToolDocsMode = DEFAULT_TOOL_DOCS_MODE,
        max_recipe_concurrency: int = 10,
    ) -> None:
        self.sandbox_config = sandbox_config or SandboxConfig()
        self.action_config = action_config or ActionConfig()
        self.context_policy = _normalize_context_policy(
            os.environ.get("ALEPH_CONTEXT_POLICY"),
            self.action_config.context_policy,
        )
        self.action_config.context_policy = self.context_policy
        self.action_config.action_policy = _normalize_action_policy(
            os.environ.get("ALEPH_ACTION_POLICY"),
            self.action_config.action_policy,
        )
        self.output_feedback = normalize_output_feedback(
            os.environ.get("ALEPH_OUTPUT_FEEDBACK", "full")
        )
        self.sub_query_config = sub_query_config or SubQueryConfig()
        self.tool_docs_mode = tool_docs_mode
        self.max_tool_response_chars = _get_env_int(
            "ALEPH_MAX_TOOL_RESPONSE_CHARS",
            DEFAULT_TOOL_RESPONSE_MAX_CHARS,
        )
        configured_recipe_concurrency = _get_env_int(
            "ALEPH_MAX_RECIPE_CONCURRENCY",
            max_recipe_concurrency,
        )
        self.max_recipe_concurrency = max(1, configured_recipe_concurrency)
        self._sessions: dict[str, _Session] = {}
        self._node_repls: dict[str, NodeREPLEnvironment] = {}
        self._remote_servers: dict[str, _RemoteServerHandle] = {}
        self._auto_pack_loaded = False
        self._streamable_http_task: asyncio.Task | None = None
        self._streamable_http_url: str | None = None
        self._streamable_http_host: str | None = None
        self._streamable_http_port: int | None = None
        self._streamable_http_path: str | None = None
        self._streamable_http_lock = asyncio.Lock()

        # MCP roots-based workspace resolution (lazy, first action tool call)
        self._mcp_roots_resolved: bool = False
        self._workspace_root_source: str = (
            "explicit"
            if self.action_config.workspace_root_explicit
            else "auto-detected"
        )

        # Import MCP lazily so it's an optional dependency
        try:
            from mcp.server.fastmcp import Context as _MCPContext, FastMCP
        except Exception as e:
            raise RuntimeError(
                'MCP support requires the `mcp` package. Install with `pip install "aleph-rlm[mcp]"`.'
            ) from e

        self._MCPContext = _MCPContext
        # Inject into module globals so PEP 563 stringified 'Context' annotations
        # resolve at runtime for FastMCP's context auto-injection.
        globals()["Context"] = _MCPContext
        self.server = FastMCP("aleph-local")
        self._register_tools()

        if self.action_config.enabled:
            self._auto_load_memory_pack()

    def _auto_load_memory_pack(self) -> None:
        if self.context_policy == "isolated":
            return
        if self._auto_pack_loaded:
            return
        self._auto_pack_loaded = True
        pack_path = self.action_config.workspace_root / MEMORY_PACK_RELATIVE_PATH
        if not pack_path.exists() or not pack_path.is_file():
            return
        try:
            if pack_path.stat().st_size > self.action_config.max_read_bytes:
                return
        except Exception:
            return
        try:
            data = pack_path.read_bytes()
            obj = json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            return

        if not isinstance(obj, dict):
            return
        try:
            _load_memory_pack_payload(
                obj,
                sessions=self._sessions,
                sandbox_config=self.sandbox_config,
                configure_session=self._configure_session,
                loop=None,
                skip_existing=True,
            )
        except Exception:
            return

    def _normalize_streamable_http_path(self, path: str) -> str:
        return _normalize_streamable_http_path_impl(path)

    def _format_streamable_http_url(self, host: str, port: int, path: str) -> str:
        return _format_streamable_http_url_impl(host, port, path)

    async def _wait_for_streamable_http_ready(
        self,
        host: str,
        port: int,
        timeout_seconds: float = 2.0,
    ) -> tuple[bool, str]:
        return await _wait_for_streamable_http_ready_impl(
            self,
            host,
            port,
            timeout_seconds=timeout_seconds,
        )

    async def _run_streamable_http_server(self, host: str, port: int) -> None:
        await _run_streamable_http_server_impl(self, host, port)

    async def _ensure_streamable_http_server(
        self,
        host: str,
        port: int,
        path: str,
    ) -> tuple[bool, str]:
        return await _ensure_streamable_http_server_impl(self, host, port, path)

    async def _ensure_internal_codex_mcp_server(self, cwd: Path | None) -> str:
        return await _ensure_internal_codex_mcp_server_impl(self, cwd)

    async def _run_internal_codex_mcp_query(
        self,
        *,
        prompt: str,
        context_slice: str | None,
        cwd: Path | None,
        mcp_server_url: str | None,
        mcp_server_name: str,
        thread_id: str | None = None,
    ) -> tuple[bool, str, str | None]:
        return await _run_internal_codex_mcp_query_impl(
            self,
            prompt=prompt,
            cwd=cwd,
            mcp_server_url=mcp_server_url,
            mcp_server_name=mcp_server_name,
            context_slice=context_slice,
            thread_id=thread_id,
        )

    async def _run_sub_query(
        self,
        *,
        prompt: str,
        context_slice: str | None,
        context_id: str,
        backend: str,
        validation_regex: str | None = None,
        max_retries: int | None = None,
        retry_prompt: str | None = None,
    ) -> tuple[bool, str, bool, str]:
        return await _run_sub_query_impl(
            self,
            prompt=prompt,
            context_slice=context_slice,
            context_id=context_id,
            backend=backend,
            validation_regex=validation_regex,
            max_retries=max_retries,
            retry_prompt=retry_prompt,
        )

    async def _run_sub_aleph(
        self,
        *,
        query: str,
        context_slice: str | None,
        context_id: str,
        current_depth: int = 1,
        root_model: str | None = None,
        sub_model: str | None = None,
        max_depth: int | None = None,
        max_iterations: int | None = None,
        max_tokens: int | None = None,
        max_sub_queries: int | None = None,
        max_wall_time_seconds: float | None = None,
        temperature: float | None = None,
    ) -> tuple[AlephResponse, dict[str, object]]:
        return await _run_sub_aleph_impl(
            self,
            query=query,
            context_slice=context_slice,
            context_id=context_id,
            current_depth=current_depth,
            root_model=root_model,
            sub_model=sub_model,
            max_depth=max_depth,
            max_iterations=max_iterations,
            max_tokens=max_tokens,
            max_sub_queries=max_sub_queries,
            max_wall_time_seconds=max_wall_time_seconds,
            temperature=temperature,
            analyze_text_context=_analyze_text_context,
        )

    @staticmethod
    def _recipe_preview(value: Any, limit: int = 180) -> str:
        return _recipe_preview_impl(value, limit=limit)

    @staticmethod
    def _recipe_context_slice(value: Any, context_field: str | None) -> str:
        return _recipe_context_slice_impl(value, context_field)

    async def _execute_recipe(
        self,
        *,
        recipe: dict[str, Any],
        context_id_override: str | None = None,
        dry_run: bool = False,
        progress_callback: Callable[[float, float | None, str | None], Any]
        | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        return await _execute_recipe_impl(
            self,
            recipe=recipe,
            context_id_override=context_id_override,
            dry_run=dry_run,
            progress_callback=progress_callback,
        )

    async def _compile_recipe_code(
        self,
        *,
        code: str,
        context_id: str = "default",
        language: str = "python",
    ) -> tuple[bool, dict[str, Any]]:
        return await _compile_recipe_code_impl(
            self,
            code=code,
            context_id=context_id,
            language=language,
        )

    def _get_sub_query_config_snapshot(self) -> dict[str, Any]:
        return get_sub_query_config_snapshot(
            self.sub_query_config,
            context_policy=self.context_policy,
        )

    def _apply_sub_query_runtime_config(
        self,
        *,
        sub_query_backend: str | None = None,
        sub_query_timeout: float | None = None,
        sub_query_share_session: bool | None = None,
    ) -> tuple[bool, str]:
        return apply_sub_query_runtime_config(
            self.sub_query_config,
            cli_backends=CLI_BACKENDS,
            sub_query_backend=sub_query_backend,
            sub_query_timeout=sub_query_timeout,
            sub_query_share_session=sub_query_share_session,
        )

    def _inject_repl_config_helpers(self, session: _Session) -> None:
        _inject_repl_config_helpers_impl(self, session)

    def _inject_repl_sub_query(self, session: _Session, context_id: str) -> None:
        _inject_repl_sub_query_impl(self, session, context_id)

    def _inject_repl_sub_aleph(self, session: _Session, context_id: str) -> None:
        _inject_repl_sub_aleph_impl(self, session, context_id)

    def _configure_session(
        self,
        session: _Session,
        context_id: str,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        _configure_session_impl(self, session, context_id, loop=loop)

    async def _ensure_remote_server(
        self, server_id: str
    ) -> tuple[bool, str | _RemoteServerHandle]:
        return await ensure_remote_server(self._remote_servers, server_id)

    async def _reset_remote_server_handle(self, handle: _RemoteServerHandle) -> None:
        await reset_remote_server_handle(handle)

    async def _close_remote_server(self, server_id: str) -> tuple[bool, str]:
        return await close_remote_server(self._remote_servers, server_id)

    async def _remote_list_tools(self, server_id: str) -> tuple[bool, Any]:
        return await remote_list_tools(
            self._remote_servers,
            server_id,
            to_jsonable=_to_jsonable,
        )

    async def _remote_call_tool(
        self,
        server_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: float | None = DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS,
    ) -> tuple[bool, Any]:
        return await remote_call_tool(
            self._remote_servers,
            server_id,
            tool,
            arguments,
            timeout_seconds=timeout_seconds,
            default_timeout_seconds=DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS,
            to_jsonable=_to_jsonable,
        )

    def _remote_tool_allowed(self, handle: _RemoteServerHandle, tool_name: str) -> bool:
        return remote_tool_allowed(handle, tool_name)

    def _format_context_loaded(
        self,
        context_id: str,
        meta: ContextMetadata,
        line_number_base: LineNumberBase,
        note: str | None = None,
    ) -> str:
        return _format_context_loaded_impl(
            context_id,
            meta,
            line_number_base,
            note=note,
        )

    def _snapshot_session_state(self, session: _Session) -> dict[str, Any]:
        return _snapshot_session_state_impl(session)

    def _restore_session_state(self, session: _Session, state: dict[str, Any]) -> None:
        _restore_session_state_impl(session, state)

    def _replace_session_context(
        self,
        context: str,
        context_id: str,
        fmt: ContentFormat,
        line_number_base: LineNumberBase,
        *,
        preserve_state: bool = False,
    ) -> ContextMetadata:
        return _replace_session_context_impl(
            sessions=self._sessions,
            context=context,
            context_id=context_id,
            fmt=fmt,
            line_number_base=line_number_base,
            sandbox_config=self.sandbox_config,
            analyze_text_context=_analyze_text_context,
            configure_session=self._configure_session,
            close_node_repl=self._close_node_repl,
            preserve_state=preserve_state,
        )

    def _create_session(
        self,
        context: str,
        context_id: str,
        fmt: ContentFormat,
        line_number_base: LineNumberBase,
    ) -> ContextMetadata:
        return _create_session_impl(
            sessions=self._sessions,
            context=context,
            context_id=context_id,
            fmt=fmt,
            line_number_base=line_number_base,
            sandbox_config=self.sandbox_config,
            analyze_text_context=_analyze_text_context,
            configure_session=self._configure_session,
            close_node_repl=self._close_node_repl,
        )

    def _get_or_create_session(
        self,
        context_id: str,
        line_number_base: LineNumberBase | None = None,
    ) -> _Session:
        return _get_or_create_session_impl(
            sessions=self._sessions,
            context_id=context_id,
            line_number_base=line_number_base,
            sandbox_config=self.sandbox_config,
            analyze_text_context=_analyze_text_context,
            configure_session=self._configure_session,
        )

    def _close_node_repl(self, context_id: str) -> None:
        _close_node_repl_impl(self._node_repls, context_id)

    def _configure_node_repl(
        self,
        node_repl: "NodeREPLEnvironment",
        session: _Session,
    ) -> None:
        _configure_node_repl_impl(node_repl, session)

    def _get_or_create_node_repl(self, context_id: str) -> "NodeREPLEnvironment":
        return _get_or_create_node_repl_impl(
            self._node_repls, self._sessions, context_id, self.sandbox_config
        )

    def _sync_session_from_node_repl(self, context_id: str) -> list[dict[str, Any]]:
        return _sync_session_from_node_repl_impl(
            self._node_repls, self._sessions, context_id, _analyze_text_context
        )

    def _first_doc_line(self, fn: Any) -> str:
        doc = inspect.getdoc(fn) or ""
        for line in doc.splitlines():
            line = line.strip()
            if line:
                return line
        return ""

    def _short_description(self, fn: Any, override: str | None) -> str:
        desc = (override or self._first_doc_line(fn)).strip()
        if not desc:
            desc = fn.__name__.replace("_", " ")
        max_len = 120
        if len(desc) > max_len:
            desc = desc[: max_len - 3].rstrip() + "..."
        return desc

    def _tool_decorator(self, description: str | None = None, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            doc = inspect.getdoc(fn) or ""
            if self.tool_docs_mode == "full" and doc:
                return self.server.tool(**kwargs)(fn)
            desc = self._short_description(fn, description)
            return self.server.tool(description=desc, **kwargs)(fn)

        return decorator

    def _require_actions(
        self,
        confirm: bool,
        *,
        requires_write: bool = False,
        requires_command: bool = False,
    ) -> str | None:
        return _require_actions_impl(
            self.action_config,
            confirm,
            requires_write=requires_write,
            requires_command=requires_command,
        )

    async def _maybe_resolve_workspace_from_roots(self, ctx: "Context") -> None:
        """Try to resolve workspace root from MCP client roots (lazy, once)."""
        if self._mcp_roots_resolved or self.action_config.workspace_root_explicit:
            return
        self._mcp_roots_resolved = True
        previous_root = self.action_config.workspace_root
        try:
            session = ctx.request_context.session
            roots = await session.list_roots()
        except Exception:
            return
        if not roots or not getattr(roots, "roots", None):
            return
        result = roots_to_workspace_root(roots.roots)
        if result is not None:
            self.action_config.workspace_root = result
            self._workspace_root_source = "mcp-roots"
            if result != previous_root and self.action_config.enabled:
                # Re-attempt memory-pack auto-load after swapping from an
                # invocation cwd (for example `/`) to the actual MCP workspace.
                self._auto_pack_loaded = False
                self._auto_load_memory_pack()
            try:
                await ctx.info(f"Workspace root resolved from MCP roots: {result}")
            except Exception:
                pass

    def _record_action(self, session: _Session | None, note: str, snippet: str) -> None:
        _record_action_impl(session, note=note, snippet=snippet)

    def _scoped_path(self, path: str) -> Path:
        return _scoped_path(
            self.action_config.workspace_root,
            path,
            self.action_config.workspace_mode,
        )

    def _build_memory_pack_payload(
        self,
        *,
        include_ctx: bool = True,
    ) -> tuple[dict[str, Any], list[str]]:
        return _build_memory_pack_payload_impl(
            self._sessions,
            include_ctx=include_ctx,
        )

    async def _run_subprocess(
        self,
        argv: list[str],
        cwd: Path,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return await _run_subprocess_impl(
            self.action_config,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )

    def _parse_rg_vimgrep(
        self, output: str, max_results: int
    ) -> tuple[list[dict[str, Any]], bool]:
        return _parse_rg_vimgrep_impl(output, max_results)

    def _python_rg_search(
        self,
        pattern: str,
        roots: list[Path],
        glob_pattern: str | None,
        max_results: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        return _python_rg_search_impl(
            pattern,
            roots,
            glob_pattern,
            max_results,
            self.action_config.max_read_bytes,
        )

    def _auto_save_memory_pack(self) -> None:
        if self.context_policy == "isolated":
            return
        if self.action_config.action_policy == "read-only":
            return
        if not self.action_config.enabled or not self._sessions:
            return
        try:
            workspace_root = self.action_config.workspace_root.resolve()
        except Exception:
            workspace_root = self.action_config.workspace_root
        if workspace_root == Path("/"):
            return
        payload, _ = self._build_memory_pack_payload(include_ctx=True)
        out_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode(
            "utf-8", errors="replace"
        )
        if len(out_bytes) > self.action_config.max_write_bytes:
            return
        try:
            p = _scoped_path(
                self.action_config.workspace_root,
                ".aleph/memory_pack.json",
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
            self._record_action(sess, note="auto_save_memory_pack", snippet=str(p))

    def _register_core_tools(self) -> None:
        _register_context_tools_module(self, format_error=_format_error)

    def _register_action_tools(self) -> None:
        _register_action_tools_module(
            self, format_error=_format_error, format_payload=_format_payload
        )

    def _format_execution_result(self, result: ExecutionResult) -> str | dict[str, Any]:
        return _format_execution_result_impl(
            result,
            max_chars=self.max_tool_response_chars,
            truncation_suffix=_TOOL_TRUNCATION_SUFFIX,
        )

    def _truncate_tool_text(
        self,
        text: str,
        *,
        max_chars: int | None = None,
    ) -> tuple[str, bool]:
        return _truncate_tool_text_impl(
            text,
            max_chars=(
                self.max_tool_response_chars if max_chars is None else max_chars
            ),
            truncation_suffix=_TOOL_TRUNCATION_SUFFIX,
        )

    def _limit_json_items(
        self,
        items: list[Any],
        *,
        max_chars: int | None = None,
    ) -> tuple[list[Any], bool]:
        return _limit_json_items_impl(
            items,
            max_chars=(
                self.max_tool_response_chars if max_chars is None else max_chars
            ),
            to_jsonable=_to_jsonable,
        )

    def _format_variable_value(self, name: str, value: Any) -> Any:
        return _format_variable_value_impl(
            name,
            value,
            max_chars=self.max_tool_response_chars,
            truncation_suffix=_TOOL_TRUNCATION_SUFFIX,
            to_jsonable=_to_jsonable,
        )

    def _register_query_tools(self) -> None:
        _register_query_tools_module(
            self,
            get_repl_helper=_get_repl_helper,
            to_internal_line_index=_to_internal_line_index,
        )

    def _register_reasoning_tools(self) -> None:
        _register_reasoning_tools_module(self, format_error=_format_error)

    def _register_mcp_tools(self) -> None:
        register_admin_tools(self, format_error=_format_error)

    def _register_workspace_tools(self) -> None:
        register_workspace_tools(self)

    def _register_tools(self) -> None:
        """Register all MCP tools."""
        self._register_core_tools()
        self._register_action_tools()
        self._register_query_tools()
        self._register_reasoning_tools()
        self._register_mcp_tools()
        self._register_workspace_tools()

    async def run(self, transport: str = "stdio") -> None:
        """Run the MCP server."""
        if transport != "stdio":
            raise ValueError("Only stdio transport is supported")

        await self.server.run_stdio_async()


def main() -> None:
    """CLI entry point: `aleph` or `python -m aleph.mcp.local_server`"""

    if len(sys.argv) > 1 and sys.argv[1] in {"run", "shell", "serve"}:
        from ..alef_cli import main as alef_main

        raise SystemExit(alef_main(sys.argv[1:]))

    parser = build_server_argument_parser(
        default_workspace_mode=DEFAULT_WORKSPACE_MODE,
        default_tool_docs_mode=DEFAULT_TOOL_DOCS_MODE,
    )
    args = parser.parse_args()
    apply_server_env_overrides(args)
    config, action_cfg, tool_docs_mode = build_runtime_configs(
        args,
        detect_workspace_root=_detect_workspace_root,
        normalize_context_policy=_normalize_context_policy,
        normalize_action_policy=_normalize_action_policy,
        default_context_policy=DEFAULT_CONTEXT_POLICY,
        default_action_policy=DEFAULT_ACTION_POLICY,
        sandbox_config_factory=SandboxConfig,
        action_config_factory=ActionConfig,
    )

    server = AlephMCPServerLocal(
        sandbox_config=config,
        action_config=action_cfg,
        tool_docs_mode=cast(ToolDocsMode, tool_docs_mode),
    )
    asyncio.run(server.run())


if __name__ == "__main__":
    main()

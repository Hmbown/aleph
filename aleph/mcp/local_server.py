"""Aleph MCP server for use with Claude Desktop, Cursor, Windsurf, etc.

This server exposes Aleph's context exploration tools and optional action tools.

Tools:
- load_context: Load text/data into sandboxed REPL
- peek_context: View character/line ranges
- search_context: Regex search with context
- semantic_search: Meaning-based search over the context
- exec_python: Execute Python code in sandbox
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

RLM recursion is available inside `exec_python` via REPL helpers
(`sub_query`, `sub_query_batch`, `sub_query_map`, `sub_aleph`).

Usage:
    aleph
"""

from __future__ import annotations

import asyncio
import bz2
from collections import OrderedDict
import difflib
import fnmatch
import gzip
import importlib
import inspect
import io
import json
import lzma
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, cast

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from ..config import AlephConfig
from ..core import Aleph
from ..prompts.system import DEFAULT_SYSTEM_PROMPT
from ..providers.registry import get_provider
from ..repl import helpers as repl_helpers
from ..repl.sandbox import REPLEnvironment, SandboxConfig
from ..types import AlephResponse, ContentFormat, ContextMetadata, ContextType, ExecutionResult
from ..sub_query import (
    SubQueryConfig,
    detect_backend,
)
from ..sub_query.config import (
    resolve_codex_mode,
    resolve_codex_model,
    resolve_codex_profile,
    resolve_codex_reasoning_effort,
)
from ..sub_query.cli_backend import run_cli_sub_query, CLI_BACKENDS
from ..sub_query.codex_mcp_backend import (
    build_codex_mcp_tool_call,
    compose_sub_query_prompt,
    extract_codex_mcp_result_text,
    suppress_mcp_notification_validation_logs,
)
from ..sub_query.api_backend import run_api_sub_query
from .admin_tools import register_admin_tools
from .query_tools import register_query_tools as _register_query_tools_module
from .recipes import estimate_recipe as _estimate_recipe
from .recipes import validate_recipe as _validate_recipe
from .reasoning_tools import register_reasoning_tools as _register_reasoning_tools_module
from .remote_servers import (
    _RemoteServerHandle,
    close_remote_server,
    ensure_remote_server,
    register_remote_server,
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
from .workspace import roots_to_workspace_root
from .session import (
    _Evidence,
    _Session,
    _coerce_context_to_text,
    _session_to_payload,
    _session_from_payload,
)

__all__ = ["AlephMCPServerLocal", "main", "mcp"]

mcp: Any


LineNumberBase = Literal[0, 1]
DEFAULT_LINE_NUMBER_BASE: LineNumberBase = 1
WorkspaceMode = Literal["fixed", "git", "any"]
DEFAULT_WORKSPACE_MODE: WorkspaceMode = "fixed"
ToolDocsMode = Literal["concise", "full"]
DEFAULT_TOOL_DOCS_MODE: ToolDocsMode = "concise"
ContextPolicy = Literal["trusted", "isolated"]
DEFAULT_CONTEXT_POLICY: ContextPolicy = "trusted"
DEFAULT_TOOL_RESPONSE_MAX_CHARS = 10_000
_TOOL_TRUNCATION_SUFFIX = "\n... [TRUNCATED]"


def _get_env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_context_policy(
    value: str | None,
    default: ContextPolicy = DEFAULT_CONTEXT_POLICY,
) -> ContextPolicy:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"trusted", "isolated"}:
        return cast(ContextPolicy, normalized)
    if normalized in {"strict", "untrusted", "shared"}:
        return "isolated"
    return default


DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS = _get_env_float(
    "ALEPH_REMOTE_TOOL_TIMEOUT",
    120.0,
)


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


def _detect_format_for_suffix(text: str, suffix: str) -> ContentFormat:
    ext = suffix.lower()
    if ext in {".jsonl", ".ndjson"}:
        return ContentFormat.JSONL
    if ext == ".csv":
        return ContentFormat.CSV
    if ext == ".json":
        return ContentFormat.JSON if _detect_format(text) == ContentFormat.JSON else ContentFormat.TEXT
    if ext in {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php", ".cs",
        ".c", ".h", ".cpp", ".hpp",
    }:
        return ContentFormat.CODE
    return _detect_format(text)


def _effective_suffix(path: Path) -> str:
    suffixes = [s.lower() for s in path.suffixes]
    if suffixes and suffixes[-1] in {".gz", ".bz2", ".xz"}:
        return suffixes[-2] if len(suffixes) > 1 else ""
    return path.suffix.lower()


def _decompress_bytes(path: Path, data: bytes) -> tuple[bytes, str | None]:
    ext = path.suffix.lower()
    if ext == ".gz":
        return gzip.decompress(data), "gzip"
    if ext == ".bz2":
        return bz2.decompress(data), "bzip2"
    if ext == ".xz":
        return lzma.decompress(data), "xz"
    return data, None


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        stripped = data.strip()
        if stripped:
            self._chunks.append(stripped)

    def text(self) -> str:
        return "\n".join(self._chunks)


def _extract_text_from_html(text: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(text)
    return parser.text()


def _extract_text_from_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for para in root.iter():
        if not para.tag.endswith("}p"):
            continue
        parts: list[str] = []
        for node in para.iter():
            if node.tag.endswith("}t") and node.text:
                parts.append(node.text)
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _extract_text_from_pdf(
    data: bytes,
    path: Path | None,
    timeout_seconds: float,
) -> tuple[str, str | None]:
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = importlib.import_module(module_name)
            reader = module.PdfReader(io.BytesIO(data))
            pages: list[str] = []
            for page in reader.pages:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text:
                    pages.append(page_text)
            text = "\n".join(pages).strip()
            if text:
                return text, None
        except Exception:
            continue

    if path is not None:
        pdf_tool = shutil.which("pdftotext")
        if pdf_tool:
            try:
                result = subprocess.run(
                    [pdf_tool, "-layout", str(path), "-"],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except Exception as e:
                return "", f"pdftotext failed: {e}"
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout, None
            stderr = result.stderr.strip()
            if stderr:
                return "", f"pdftotext error: {stderr}"

    return "", "PDF extraction unavailable. Install `pypdf` or `pdftotext` for best results."


def _load_text_from_path(
    path: Path,
    max_bytes: int,
    timeout_seconds: float,
) -> tuple[str, ContentFormat, str | None]:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"File too large to read (>{max_bytes} bytes): {path}")

    data, compression = _decompress_bytes(path, data)
    if compression and len(data) > max_bytes:
        raise ValueError(f"Decompressed file too large (>{max_bytes} bytes): {path}")

    suffix = _effective_suffix(path)
    warning: str | None = None

    if suffix == ".pdf":
        text, warning = _extract_text_from_pdf(data, path, timeout_seconds)
        if not text.strip():
            raise ValueError(warning or "Failed to extract PDF text")
    elif suffix == ".docx":
        try:
            text = _extract_text_from_docx(data)
        except Exception as e:
            raise ValueError(f"Failed to extract DOCX text: {e}") from e
        if not text.strip():
            warning = "DOCX extraction produced empty text"
    elif suffix in {".html", ".htm"}:
        text = _extract_text_from_html(data.decode("utf-8", errors="replace"))
    else:
        text = data.decode("utf-8", errors="replace")

    fmt = _detect_format_for_suffix(text, suffix)
    return text, fmt, warning


_ANALYZE_CACHE_MAX = 64
_ANALYZE_CACHE: OrderedDict[tuple[int, int, ContentFormat], ContextMetadata] = OrderedDict()


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


_FINAL_RE = re.compile(r"FINAL\((.*?)\)", re.DOTALL)
_FINAL_VAR_RE = re.compile(r"FINAL_VAR\((.*?)\)", re.DOTALL)


def _extract_final_answer(text: str) -> tuple[str, bool]:
    match = _FINAL_RE.search(text)
    if match:
        return match.group(1).strip(), True
    match_var = _FINAL_VAR_RE.search(text)
    if match_var:
        raw = match_var.group(1).strip()
        if len(raw) >= 2 and ((raw[0] == raw[-1] == '"') or (raw[0] == raw[-1] == "'")):
            raw = raw[1:-1].strip()
        return raw, True
    return text.strip(), False


def _build_sub_aleph_cli_prompt(
    *,
    query: str,
    context_slice: str,
    context_format: ContentFormat,
    cfg: AlephConfig,
) -> str:
    meta = _analyze_text_context(context_slice, context_format)
    system_template = cfg.system_prompt or DEFAULT_SYSTEM_PROMPT
    system_prompt = system_template.format(
        query=query,
        context_var=cfg.context_var_name,
        context_format=meta.format.value,
        context_size_chars=meta.size_chars,
        context_size_lines=meta.size_lines,
        context_size_tokens=meta.size_tokens_estimate,
        context_preview="[OMITTED FOR CONTEXT ISOLATION]",
        structure_hint=meta.structure_hint or "N/A",
    )
    instructions = (
        "SINGLE-SHOT MODE (no live Python REPL in this call):\n"
        "- Do not output code blocks.\n"
        "- Answer directly and wrap the final answer in FINAL(...).\n"
    )
    return f"{system_prompt}\n\n{instructions}\nQUERY:\n{query}"


def _resolve_env_dir(name: str, require_exists: bool = True) -> Path | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        path = Path(value).expanduser()
    except Exception:
        return None
    if require_exists and not path.exists():
        return None
    try:
        path = path.resolve()
    except Exception:
        pass
    if path.is_file():
        return path.parent
    return path


def _detect_workspace_root() -> Path:
    env_root = _resolve_env_dir("ALEPH_WORKSPACE_ROOT", require_exists=False)
    if env_root is not None:
        return env_root
    cwd = _resolve_env_dir("PWD") or _resolve_env_dir("INIT_CWD") or Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    return cwd


def _nearest_existing_parent(path: Path) -> Path:
    for parent in [path, *path.parents]:
        if parent.exists():
            return parent
    return path


def _find_git_root(path: Path) -> Path | None:
    start = _nearest_existing_parent(path)
    if start.is_file():
        start = start.parent
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _scoped_path(workspace_root: Path, path: str, mode: WorkspaceMode) -> Path:
    root = workspace_root.resolve()
    p = Path(path)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (root / p).resolve()

    if mode == "any":
        return resolved

    if mode == "git":
        git_root = _find_git_root(resolved)
        if git_root is None:
            raise ValueError(f"Path '{path}' is not inside a git repository (workspace mode: git)")
        if not resolved.is_relative_to(git_root):
            raise ValueError(f"Path '{path}' escapes git root '{git_root}'")
        return resolved

    if not resolved.is_relative_to(root):
        raise ValueError(f"Path '{path}' escapes workspace root '{root}'")
    return resolved


def _format_payload(
    payload: dict[str, Any],
    output: Literal["json", "markdown", "object"],
) -> str | dict[str, Any]:
    def _truncate_inline(text: str, limit: int) -> str:
        if limit <= 0 or len(text) <= limit:
            return text
        if limit <= len(_TOOL_TRUNCATION_SUFFIX):
            return _TOOL_TRUNCATION_SUFFIX[:limit]
        preview_each_side = min(400, max(0, (limit - len(_TOOL_TRUNCATION_SUFFIX)) // 2))
        if preview_each_side == 0:
            keep = limit - len(_TOOL_TRUNCATION_SUFFIX)
            return text[:keep] + _TOOL_TRUNCATION_SUFFIX
        return (
            text[:preview_each_side]
            + _TOOL_TRUNCATION_SUFFIX
            + text[-preview_each_side:]
        )

    def _sanitize(value: Any, *, key: str | None = None) -> Any:
        if key == "ctx":
            text = _coerce_context_to_text(value)
            return {
                "redacted": True,
                "reason": "context_field_blocked",
                "original_chars": len(text),
                "value_preview": _truncate_inline(text, min(200, DEFAULT_TOOL_RESPONSE_MAX_CHARS)),
            }

        if isinstance(value, dict):
            return {
                str(k): _sanitize(v, key=str(k))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_sanitize(v, key=key) for v in value]
        if isinstance(value, str):
            return _truncate_inline(value, DEFAULT_TOOL_RESPONSE_MAX_CHARS)
        return value

    safe_payload = cast(dict[str, Any], _sanitize(payload))
    if output == "object":
        return safe_payload

    rendered = json.dumps(safe_payload, ensure_ascii=False, indent=2)
    if output == "json":
        return _truncate_inline(rendered, DEFAULT_TOOL_RESPONSE_MAX_CHARS)

    fence_overhead = len("```json\n\n```")
    json_limit = max(0, DEFAULT_TOOL_RESPONSE_MAX_CHARS - fence_overhead)
    rendered = _truncate_inline(rendered, json_limit)
    return "```json\n" + rendered + "\n```"


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


def _to_internal_line_index(index: int | None, base: LineNumberBase) -> int | None:
    """Convert external line indices (line_number_base) to internal 0-based indices."""

    if index is None or index < 0:
        return index
    if base == 0:
        return index
    if index == 0:
        # Backward-compatible handling for older callers that still pass 0-based values.
        return 0
    return index - 1


def _resolve_session_payload_id(session_payload: Any) -> str | None:
    """Resolve a session identifier from a memory-pack session payload."""

    if not isinstance(session_payload, dict):
        return None
    for key in ("id", "context_id", "session_id"):
        value = session_payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _get_repl_helper(repl: REPLEnvironment, name: str) -> object | None:
    """Return a helper callable, preferring stable helper references."""

    get_helper = getattr(repl, "get_helper", None)
    if callable(get_helper):
        helper = get_helper(name)
        if helper is not None:
            return helper
    return repl.get_variable(name)


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
    workspace_mode: WorkspaceMode = DEFAULT_WORKSPACE_MODE
    context_policy: ContextPolicy = DEFAULT_CONTEXT_POLICY
    require_confirmation: bool = False
    max_cmd_seconds: float = 60.0
    max_output_chars: int = 50_000
    max_read_bytes: int = 1_000_000_000   # Default 1GB. Increase if you have more RAM - the LLM only sees query results, not the file.
    max_write_bytes: int = 100_000_000    # 100 MB
    workspace_root_explicit: bool = False  # True when set via CLI arg, env var, or configure()

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
        self.output_feedback: str = os.environ.get("ALEPH_OUTPUT_FEEDBACK", "full")
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
            "explicit" if self.action_config.workspace_root_explicit else "auto-detected"
        )

        # Import MCP lazily so it's an optional dependency
        try:
            from mcp.server.fastmcp import Context as _MCPContext, FastMCP
        except Exception as e:
            raise RuntimeError(
                "MCP support requires the `mcp` package. Install with `pip install \"aleph-rlm[mcp]\"`."
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
        pack_path = self.action_config.workspace_root / ".aleph" / "memory_pack.json"
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
        if obj.get("schema") != "aleph.memory_pack.v1":
            return
        sessions = obj.get("sessions")
        if not isinstance(sessions, list):
            return
        for payload in sessions:
            if not isinstance(payload, dict):
                continue
            session_id = payload.get("context_id") or payload.get("session_id")
            resolved_id = str(session_id) if session_id else f"session_{len(self._sessions) + 1}"
            if resolved_id in self._sessions:
                continue
            try:
                session = _session_from_payload(payload, resolved_id, self.sandbox_config, loop=None)
            except Exception:
                continue
            self._configure_session(session, resolved_id, loop=None)
            self._sessions[resolved_id] = session

    def _normalize_streamable_http_path(self, path: str) -> str:
        if not path:
            return "/mcp"
        return path if path.startswith("/") else f"/{path}"

    def _format_streamable_http_url(self, host: str, port: int, path: str) -> str:
        connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        return f"http://{connect_host}:{port}{path}"

    async def _wait_for_streamable_http_ready(
        self,
        host: str,
        port: int,
        timeout_seconds: float = 2.0,
    ) -> tuple[bool, str]:
        deadline = time.monotonic() + timeout_seconds
        connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host

        while time.monotonic() < deadline:
            if self._streamable_http_task and self._streamable_http_task.done():
                exc = self._streamable_http_task.exception()
                if exc:
                    return False, f"Streamable HTTP server failed to start: {exc}"
                return False, "Streamable HTTP server stopped unexpectedly."
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(connect_host, port),
                    timeout=0.2,
                )
                writer.close()
                await writer.wait_closed()
                return True, ""
            except Exception:
                await asyncio.sleep(0.05)

        return False, f"Timed out waiting for streamable HTTP server on {connect_host}:{port}."

    async def _run_streamable_http_server(self, host: str, port: int) -> None:
        try:
            import uvicorn
        except Exception as exc:
            raise RuntimeError(
                "uvicorn is required for streamable HTTP transport. "
                "Install with: pip install uvicorn"
            ) from exc

        app = self.server.streamable_http_app()
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def _ensure_streamable_http_server(
        self,
        host: str,
        port: int,
        path: str,
    ) -> tuple[bool, str]:
        normalized_path = self._normalize_streamable_http_path(path)
        async with self._streamable_http_lock:
            if self._streamable_http_task and not self._streamable_http_task.done():
                url = self._streamable_http_url or self._format_streamable_http_url(
                    host,
                    port,
                    normalized_path,
                )
                return True, url
            if self._streamable_http_task and self._streamable_http_task.done():
                self._streamable_http_task = None
                self._streamable_http_url = None

            self.server.settings.host = host
            self.server.settings.port = port
            self.server.settings.streamable_http_path = normalized_path

            self._streamable_http_task = asyncio.create_task(
                self._run_streamable_http_server(host, port)
            )
            self._streamable_http_host = host
            self._streamable_http_port = port
            self._streamable_http_path = normalized_path
            self._streamable_http_url = self._format_streamable_http_url(
                host,
                port,
                normalized_path,
            )

        ok, err = await self._wait_for_streamable_http_ready(host, port)
        if not ok:
            return False, err
        return True, self._streamable_http_url or self._format_streamable_http_url(
            host,
            port,
            normalized_path,
        )

    async def _ensure_internal_codex_mcp_server(self, cwd: Path | None) -> str:
        server_id = "__aleph_internal_codex__"
        handle = self._remote_servers.get(server_id)
        if handle is None:
            handle = register_remote_server(
                self._remote_servers,
                server_id,
                command="codex",
                args=["mcp-server", "-c", "mcp_servers={}"],
                cwd=cwd,
                allow_tools=["codex", "codex-reply"],
            )
        elif handle.cwd != cwd:
            await self._reset_remote_server_handle(handle)
            handle.cwd = cwd

        with suppress_mcp_notification_validation_logs():
            ok, res = await self._ensure_remote_server(server_id)
        if not ok:
            raise RuntimeError(str(res))
        return server_id

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
        full_prompt = compose_sub_query_prompt(prompt, context_slice)

        tool_name, arguments = build_codex_mcp_tool_call(
            prompt=full_prompt,
            cwd=cwd,
            mcp_server_url=mcp_server_url,
            mcp_server_name=mcp_server_name,
            trust_mcp_server=True,
            model=resolve_codex_model(self.sub_query_config.codex_model),
            reasoning_effort=resolve_codex_reasoning_effort(
                self.sub_query_config.codex_reasoning_effort
            ),
            profile=resolve_codex_profile(self.sub_query_config.codex_profile),
            thread_id=thread_id,
        )

        try:
            server_id = await self._ensure_internal_codex_mcp_server(cwd)
        except Exception as e:
            return False, f"Failed to start internal Codex MCP server: {e}", None

        with suppress_mcp_notification_validation_logs():
            ok, result = await self._remote_call_tool(
                server_id,
                tool_name,
                arguments,
                timeout_seconds=self.sub_query_config.cli_timeout_seconds,
            )
        if not ok:
            return False, str(result), None

        output, resolved_thread_id = extract_codex_mcp_result_text(result)
        if not output:
            output = json.dumps(_to_jsonable(result), ensure_ascii=True)

        if len(output) > self.sub_query_config.cli_max_output_chars:
            output = output[: self.sub_query_config.cli_max_output_chars] + "\n...[truncated]"

        return True, output, resolved_thread_id

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
        session = self._sessions.get(context_id)
        if session:
            session.iterations += 1

        truncated = False
        if context_slice and len(context_slice) > self.sub_query_config.max_context_chars:
            context_slice = context_slice[: self.sub_query_config.max_context_chars]
            truncated = True

        resolved_backend = backend
        if backend == "auto":
            resolved_backend = detect_backend(self.sub_query_config)

        allowed_backends = {"auto", "api", *CLI_BACKENDS}
        if resolved_backend not in allowed_backends:
            allowed_list = ", ".join(sorted(allowed_backends))
            return (
                False,
                f"Unsupported backend '{resolved_backend}'. Choose from: {allowed_list}.",
                truncated,
                resolved_backend,
            )

        resolved_validation_regex = validation_regex
        if resolved_validation_regex is None:
            resolved_validation_regex = (
                self.sub_query_config.validation_regex
                or os.environ.get("ALEPH_SUB_QUERY_VALIDATION_REGEX")
            )
        if resolved_validation_regex is not None:
            resolved_validation_regex = resolved_validation_regex.strip()
            if not resolved_validation_regex:
                resolved_validation_regex = None

        resolved_max_retries = self.sub_query_config.max_retries if max_retries is None else max_retries
        if max_retries is None:
            resolved_max_retries = _get_env_int("ALEPH_SUB_QUERY_MAX_RETRIES", resolved_max_retries)

        resolved_retry_prompt = (
            self.sub_query_config.retry_prompt if retry_prompt is None else retry_prompt
        )
        if retry_prompt is None:
            env_retry_prompt = os.environ.get("ALEPH_SUB_QUERY_RETRY_PROMPT")
            if env_retry_prompt:
                resolved_retry_prompt = env_retry_prompt

        validation_re: re.Pattern[str] | None = None
        if resolved_validation_regex:
            try:
                validation_re = re.compile(resolved_validation_regex, re.MULTILINE)
            except re.error as e:
                return False, f"Invalid validation regex: {e}", truncated, resolved_backend

        attempt = 0
        base_prompt = prompt
        prompt_for_attempt = base_prompt
        codex_thread_id: str | None = None

        try:
            while True:
                run_prompt = prompt_for_attempt
                if resolved_backend in CLI_BACKENDS:
                    mcp_server_url = None
                    server_name = "aleph_shared"
                    share_session = _get_env_bool("ALEPH_SUB_QUERY_SHARE_SESSION", False)
                    if share_session and resolved_backend in {"claude", "codex", "gemini", "kimi"}:
                        host = os.environ.get("ALEPH_SUB_QUERY_HTTP_HOST", "127.0.0.1")
                        port = _get_env_int("ALEPH_SUB_QUERY_HTTP_PORT", 8765)
                        path = os.environ.get("ALEPH_SUB_QUERY_HTTP_PATH", "/mcp")
                        server_name = os.environ.get(
                            "ALEPH_SUB_QUERY_MCP_SERVER_NAME",
                            "aleph_shared",
                        ).strip() or "aleph_shared"
                        ok, url_or_err = await self._ensure_streamable_http_server(host, port, path)
                        if not ok:
                            return False, f"Failed to start streamable HTTP server: {url_or_err}", truncated, resolved_backend
                        mcp_server_url = url_or_err
                        run_prompt = (
                            f"{run_prompt}\n\n"
                            f"[MCP tools are available via the live Aleph server. "
                            f"Use context_id={context_id!r} when calling tools. "
                            f"Tools are prefixed with `mcp__{server_name}__`.]"
                        )
                    cwd = self.action_config.workspace_root if self.action_config.enabled else None
                    if resolved_backend == "codex" and resolve_codex_mode(
                        self.sub_query_config.codex_mode
                    ) == "mcp":
                        success, output, codex_thread_id = await self._run_internal_codex_mcp_query(
                            prompt=run_prompt,
                            context_slice=context_slice,
                            cwd=cwd,
                            mcp_server_url=mcp_server_url,
                            mcp_server_name=server_name,
                            thread_id=codex_thread_id,
                        )
                    else:
                        success, output = await run_cli_sub_query(
                            prompt=run_prompt,
                            context_slice=context_slice,
                            backend=resolved_backend,  # type: ignore[arg-type]
                            timeout=self.sub_query_config.cli_timeout_seconds,
                            cwd=cwd,
                            max_output_chars=self.sub_query_config.cli_max_output_chars,
                            max_context_chars=self.sub_query_config.max_context_chars,
                            mcp_server_url=mcp_server_url,
                            mcp_server_name=server_name,
                            trust_mcp_server=True,
                            claude_model=self.sub_query_config.claude_model,
                            claude_effort=self.sub_query_config.claude_effort,
                            codex_mode=self.sub_query_config.codex_mode,
                            codex_model=self.sub_query_config.codex_model,
                            codex_reasoning_effort=self.sub_query_config.codex_reasoning_effort,
                            codex_profile=self.sub_query_config.codex_profile,
                        )
                else:
                    success, output = await run_api_sub_query(
                        prompt=run_prompt,
                        context_slice=context_slice,
                        model=self.sub_query_config.api_model,
                        api_key_env=self.sub_query_config.api_key_env,
                        api_base_url_env=self.sub_query_config.api_base_url_env,
                        api_model_env=self.sub_query_config.api_model_env,
                        timeout=self.sub_query_config.api_timeout_seconds,
                        system_prompt=self.sub_query_config.system_prompt if self.sub_query_config.include_system_prompt else None,
                        max_context_chars=self.sub_query_config.max_context_chars,
                    )

                if not success:
                    break

                if validation_re and not validation_re.search(output):
                    if attempt >= resolved_max_retries:
                        success = False
                        output = (
                            f"Output failed validation regex {resolved_validation_regex!r} "
                            f"after {attempt + 1} attempt(s). Last output: {output}"
                        )
                        break
                    attempt += 1
                    prompt_for_attempt = (
                        f"{base_prompt}\n\n"
                        f"{resolved_retry_prompt}\n"
                        f"Required format regex: {resolved_validation_regex}"
                    )
                    continue

                break
        except Exception as e:
            success = False
            output = f"{type(e).__name__}: {e}"

        if session:
            note_parts = [f"backend={resolved_backend}"]
            if resolved_validation_regex:
                note_parts.append(f"validation={resolved_validation_regex!r}")
                if attempt:
                    note_parts.append(f"retries={attempt}")
            if truncated:
                note_parts.append("truncated_context")
            session.evidence.append(_Evidence(
                source="sub_query",
                line_range=None,
                pattern=None,
                snippet=output[:200] if success else f"[ERROR] {output[:150]}",
                note=" ".join(note_parts),
            ))
            session.information_gain.append(1 if success else 0)

        return success, output, truncated, resolved_backend

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
        session = self._sessions.get(context_id)
        if session:
            session.iterations += 1
            session.max_depth_seen = max(session.max_depth_seen, current_depth)

        cfg = AlephConfig.from_env()
        budget = cfg.to_budget()
        if max_tokens is not None:
            budget.max_tokens = max_tokens
        if max_iterations is not None:
            budget.max_iterations = max_iterations
        if max_depth is not None:
            budget.max_depth = max_depth
        if max_wall_time_seconds is not None:
            budget.max_wall_time_seconds = max_wall_time_seconds
        if max_sub_queries is not None:
            budget.max_sub_queries = max_sub_queries

        resolved_root = root_model or cfg.root_model
        resolved_sub = sub_model or cfg.sub_model or resolved_root

        temp_val = 0.0
        if temperature is not None:
            try:
                temp_val = float(temperature)
            except (TypeError, ValueError):
                temp_val = 0.0

        resolved_backend = detect_backend(self.sub_query_config)
        truncated_context = False
        start_time = time.perf_counter()

        if resolved_backend in CLI_BACKENDS:
            cli_context = context_slice or ""
            if cli_context and len(cli_context) > self.sub_query_config.max_context_chars:
                cli_context = cli_context[: self.sub_query_config.max_context_chars]
                truncated_context = True

            context_format = session.meta.format if session else ContentFormat.TEXT
            prompt = _build_sub_aleph_cli_prompt(
                query=query,
                context_slice=cli_context,
                context_format=context_format,
                cfg=cfg,
            )

            mcp_server_url = None
            server_name = "aleph_shared"
            share_session = _get_env_bool("ALEPH_SUB_QUERY_SHARE_SESSION", False)
            if share_session and resolved_backend in {"claude", "codex", "gemini", "kimi"}:
                host = os.environ.get("ALEPH_SUB_QUERY_HTTP_HOST", "127.0.0.1")
                port = _get_env_int("ALEPH_SUB_QUERY_HTTP_PORT", 8765)
                path = os.environ.get("ALEPH_SUB_QUERY_HTTP_PATH", "/mcp")
                server_name = os.environ.get(
                    "ALEPH_SUB_QUERY_MCP_SERVER_NAME",
                    "aleph_shared",
                ).strip() or "aleph_shared"
                ok, url_or_err = await self._ensure_streamable_http_server(host, port, path)
                if not ok:
                    response = AlephResponse(
                        answer="",
                        success=False,
                        total_iterations=0,
                        max_depth_reached=0,
                        total_tokens=0,
                        total_cost_usd=0.0,
                        wall_time_seconds=time.perf_counter() - start_time,
                        trajectory=[],
                        error=f"Failed to start streamable HTTP server: {url_or_err}",
                        error_type="cli_error",
                    )
                else:
                    mcp_server_url = url_or_err
                    prompt = (
                        f"{prompt}\n\n"
                        f"[MCP tools are available via the live Aleph server. "
                        f"Use context_id={context_id!r} when calling tools. "
                        f"Tools are prefixed with `mcp__{server_name}__`.]"
                    )

            if mcp_server_url is not None or not share_session:
                try:
                    cwd = self.action_config.workspace_root if self.action_config.enabled else None
                    if resolved_backend == "codex" and resolve_codex_mode(
                        self.sub_query_config.codex_mode
                    ) == "mcp":
                        success, output, _thread_id = await self._run_internal_codex_mcp_query(
                            prompt=prompt,
                            context_slice=cli_context if cli_context else None,
                            cwd=cwd,
                            mcp_server_url=mcp_server_url,
                            mcp_server_name=server_name,
                        )
                    else:
                        success, output = await run_cli_sub_query(
                            prompt=prompt,
                            context_slice=cli_context if cli_context else None,
                            backend=resolved_backend,  # type: ignore[arg-type]
                            timeout=self.sub_query_config.cli_timeout_seconds,
                            cwd=cwd,
                            max_output_chars=self.sub_query_config.cli_max_output_chars,
                            max_context_chars=self.sub_query_config.max_context_chars,
                            mcp_server_url=mcp_server_url,
                            mcp_server_name=server_name,
                            trust_mcp_server=True,
                            claude_model=self.sub_query_config.claude_model,
                            claude_effort=self.sub_query_config.claude_effort,
                            codex_mode=self.sub_query_config.codex_mode,
                            codex_model=self.sub_query_config.codex_model,
                            codex_reasoning_effort=self.sub_query_config.codex_reasoning_effort,
                            codex_profile=self.sub_query_config.codex_profile,
                        )
                except Exception as e:
                    success, output = False, f"{type(e).__name__}: {e}"

                wall_time = time.perf_counter() - start_time
                if success:
                    answer, _ = _extract_final_answer(output)
                    if not answer:
                        response = AlephResponse(
                            answer="",
                            success=False,
                            total_iterations=current_depth,
                            max_depth_reached=current_depth,
                            total_tokens=0,
                            total_cost_usd=0.0,
                            wall_time_seconds=wall_time,
                            trajectory=[],
                            error="Empty response from CLI backend",
                            error_type="cli_error",
                        )
                    else:
                        response = AlephResponse(
                            answer=answer,
                            success=True,
                            total_iterations=current_depth,
                            max_depth_reached=current_depth,
                            total_tokens=0,
                            total_cost_usd=0.0,
                            wall_time_seconds=wall_time,
                            trajectory=[],
                        )
                else:
                    response = AlephResponse(
                        answer="",
                        success=False,
                        total_iterations=current_depth,
                        max_depth_reached=current_depth,
                        total_tokens=0,
                        total_cost_usd=0.0,
                        wall_time_seconds=wall_time,
                        trajectory=[],
                        error=output,
                        error_type="cli_error",
                    )
        else:
            try:
                provider = get_provider(cfg.provider, api_key=cfg.api_key)
                runner = Aleph(
                    provider=provider,
                    root_model=resolved_root,
                    sub_model=resolved_sub,
                    budget=budget,
                    sandbox_config=self.sandbox_config,
                    system_prompt=cfg.system_prompt,
                    enable_caching=cfg.enable_caching,
                    log_trajectory=cfg.log_trajectory,
                )
                response = await runner.complete(
                    query=query,
                    context=context_slice or "",
                    root_model=resolved_root,
                    sub_model=resolved_sub,
                    budget=budget,
                    temperature=temp_val,
                )
            except Exception as e:
                response = AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=0,
                    max_depth_reached=0,
                    total_tokens=0,
                    total_cost_usd=0.0,
                    wall_time_seconds=0.0,
                    trajectory=[],
                    error=str(e),
                    error_type="provider_error",
                )

        if session:
            note_parts = [f"backend={resolved_backend}", f"models={resolved_root}/{resolved_sub}"]
            if budget.max_depth is not None:
                note_parts.append(f"max_depth={budget.max_depth}")
            if truncated_context:
                note_parts.append("truncated_context")
            session.evidence.append(_Evidence(
                source="sub_aleph",
                line_range=None,
                pattern=None,
                snippet=response.answer[:200] if response.success else f"[ERROR] {str(response.error)[:150]}",
                note=" ".join(note_parts),
            ))
            session.information_gain.append(1 if response.success else 0)

        meta: dict[str, object] = {
            "root_model": resolved_root,
            "sub_model": resolved_sub,
            "budget": budget,
            "temperature": temp_val,
            "backend": resolved_backend,
            "truncated_context": truncated_context,
        }
        return response, meta

    @staticmethod
    def _recipe_preview(value: Any, limit: int = 180) -> str:
        text = _coerce_context_to_text(value)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _recipe_context_slice(value: Any, context_field: str | None) -> str:
        selected = value
        if context_field:
            if isinstance(value, dict):
                selected = value.get(context_field)
            elif isinstance(value, list):
                extracted: list[Any] = []
                for item in value:
                    if isinstance(item, dict):
                        extracted.append(item.get(context_field))
                    else:
                        extracted.append(item)
                selected = extracted
        return _coerce_context_to_text(selected)

    async def _execute_recipe(
        self,
        *,
        recipe: dict[str, Any],
        context_id_override: str | None = None,
        dry_run: bool = False,
        progress_callback: Callable[[float, float | None, str | None], Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        normalized, errors = _validate_recipe(recipe)
        if errors:
            return False, {"errors": errors}
        assert normalized is not None

        resolved_context_id = context_id_override or normalized["context_id"]
        if resolved_context_id not in self._sessions:
            return False, {"error": f"No context loaded with ID '{resolved_context_id}'."}

        estimate = _estimate_recipe(normalized)
        if dry_run:
            return True, {
                "context_id": resolved_context_id,
                "mode": "dry_run",
                "recipe": normalized,
                "estimate": estimate,
            }

        session = self._sessions[resolved_context_id]
        budget = normalized["budget"]
        max_steps = int(budget["max_steps"])
        max_sub_queries = int(budget["max_sub_queries"])

        current: Any = session.repl.get_variable("ctx")
        variables: dict[str, Any] = {"ctx": current}
        trace: list[dict[str, Any]] = []
        sub_queries_used = 0
        total_steps = float(len(normalized["steps"]))

        async def _report(progress: float, total: float | None = None, message: str | None = None) -> None:
            if progress_callback is not None:
                try:
                    result = progress_callback(progress, total, message)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

        for step_index, step in enumerate(normalized["steps"], 1):
            if step_index > max_steps:
                return False, {
                    "error": f"Recipe exceeded budget.max_steps ({step_index} > {max_steps})",
                    "failed_step": step_index,
                    "trace": trace,
                }

            session.iterations += 1

            input_name = step.get("input")
            if input_name:
                if input_name not in variables:
                    return False, {
                        "error": f"Step {step_index}: input variable '{input_name}' not found.",
                        "failed_step": step_index,
                        "trace": trace,
                    }
                current = variables[input_name]

            op = step["op"]
            step_trace: dict[str, Any] = {
                "step": step_index,
                "op": op,
            }

            try:
                if op == "search":
                    current = repl_helpers.search(
                        current,
                        step["pattern"],
                        context_lines=step.get("context_lines", 2),
                        max_results=step.get("max_results", 20),
                    )
                    step_trace["result_count"] = len(current) if isinstance(current, list) else 0

                elif op == "peek":
                    current = repl_helpers.peek(
                        current,
                        start=step.get("start", 0),
                        end=step.get("end"),
                    )

                elif op == "lines":
                    current = repl_helpers.lines(
                        current,
                        start=step.get("start", 0),
                        end=step.get("end"),
                    )

                elif op == "take":
                    count = int(step["count"])
                    if isinstance(current, str):
                        current = current[:count]
                    elif isinstance(current, (list, tuple)):
                        current = list(current)[:count]
                    else:
                        raise ValueError("take requires a list/tuple/string value")

                elif op == "chunk":
                    text = _coerce_context_to_text(current)
                    chunk_size = int(step["chunk_size"])
                    overlap = int(step.get("overlap", 0))
                    current = repl_helpers.chunk(text, chunk_size, overlap)
                    step_trace["result_count"] = len(current)

                elif op == "filter":
                    if not isinstance(current, list):
                        raise ValueError("filter requires current value to be a list")
                    field_name = step.get("field")
                    pattern = step.get("pattern")
                    contains = step.get("contains")
                    rx = re.compile(pattern) if pattern else None
                    out: list[Any] = []
                    for item in current:
                        candidate: Any = item
                        if field_name:
                            if isinstance(item, dict):
                                candidate = item.get(field_name)
                            else:
                                candidate = None
                        candidate_text = _coerce_context_to_text(candidate)
                        matched = True
                        if rx is not None:
                            matched = bool(rx.search(candidate_text))
                        if contains is not None:
                            matched = matched and contains in candidate_text
                        if matched:
                            out.append(item)
                    current = out
                    step_trace["result_count"] = len(current)

                elif op == "assign":
                    variables[step["name"]] = current

                elif op == "load":
                    name = step["name"]
                    if name not in variables:
                        raise ValueError(f"variable '{name}' not found")
                    current = variables[name]

                elif op == "map_sub_query":
                    if not isinstance(current, list):
                        raise ValueError("map_sub_query requires current value to be a list")

                    limit = step.get("limit")
                    items = current[:limit] if isinstance(limit, int) else current
                    parallel = step.get("parallel", True)
                    continue_on_error = step.get("continue_on_error", False)

                    remaining_budget = max_sub_queries - sub_queries_used
                    if len(items) > remaining_budget:
                        raise RuntimeError(
                            f"Recipe sub-query budget would be exceeded "
                            f"({sub_queries_used} + {len(items)} > {max_sub_queries})"
                        )

                    if parallel and len(items) > 1:
                        # Parallel execution with bounded concurrency
                        parallel_limit = max(1, min(self.max_recipe_concurrency, len(items)))
                        sem = asyncio.Semaphore(parallel_limit)

                        async def _run_item(idx: int, item: object) -> tuple[int, bool, str]:
                            async with sem:
                                ctx_slice = self._recipe_context_slice(item, step.get("context_field"))
                                ok, out, _trunc, _bk = await self._run_sub_query(
                                    prompt=step["prompt"],
                                    context_slice=ctx_slice,
                                    context_id=resolved_context_id,
                                    backend=step.get("backend", "auto"),
                                )
                                return idx, ok, out

                        tasks = [_run_item(i, it) for i, it in enumerate(items)]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        outputs: list[str] = [""] * len(items)
                        for r in results:
                            if isinstance(r, BaseException):
                                if not continue_on_error:
                                    raise RuntimeError(f"sub_query failed: {r}")
                                outputs[0] = f"[ERROR] {r}"  # placeholder
                            else:
                                idx, ok, out = r
                                if not ok and not continue_on_error:
                                    raise RuntimeError(f"sub_query failed: {out}")
                                outputs[idx] = out if ok else f"[ERROR] {out}"
                        sub_queries_used += len(items)
                    else:
                        # Sequential fallback
                        outputs = []
                        for item in items:
                            context_slice = self._recipe_context_slice(item, step.get("context_field"))
                            success, output, _truncated, _backend = await self._run_sub_query(
                                prompt=step["prompt"],
                                context_slice=context_slice,
                                context_id=resolved_context_id,
                                backend=step.get("backend", "auto"),
                            )
                            sub_queries_used += 1
                            if not success and not continue_on_error:
                                raise RuntimeError(f"sub_query failed: {output}")
                            outputs.append(output if success else f"[ERROR] {output}")
                    await _report(
                        float(step_index),
                        total_steps,
                        f"map_sub_query: {len(items)} items processed",
                    )

                    current = outputs
                    step_trace["sub_queries"] = len(outputs)
                    step_trace["parallel"] = parallel and len(items) > 1

                elif op in {"sub_query", "aggregate"}:
                    if sub_queries_used >= max_sub_queries:
                        raise RuntimeError(
                            "Recipe sub-query budget exceeded "
                            f"({sub_queries_used} >= {max_sub_queries})"
                        )

                    if op == "aggregate" and isinstance(current, list):
                        context_slice = "\n\n".join(
                            _coerce_context_to_text(item) for item in current
                        )
                    else:
                        context_slice = self._recipe_context_slice(
                            current, step.get("context_field")
                        )

                    success, output, _truncated, _backend = await self._run_sub_query(
                        prompt=step["prompt"],
                        context_slice=context_slice,
                        context_id=resolved_context_id,
                        backend=step.get("backend", "auto"),
                    )
                    sub_queries_used += 1
                    if not success:
                        raise RuntimeError(f"sub_query failed: {output}")
                    current = output
                    step_trace["sub_queries"] = 1

                elif op == "finalize":
                    step_trace["status"] = "finalized"
                    trace.append(step_trace)
                    break

                else:
                    raise ValueError(f"unsupported op: {op}")
            except Exception as e:
                step_trace["status"] = "error"
                step_trace["error"] = str(e)
                trace.append(step_trace)
                session.evidence.append(
                    _Evidence(
                        source="exec",
                        line_range=None,
                        pattern=None,
                        note=f"run_recipe failed at step {step_index}",
                        snippet=f"{op}: {str(e)[:180]}",
                    )
                )
                return False, {
                    "error": f"Step {step_index} ({op}) failed: {e}",
                    "failed_step": step_index,
                    "trace": trace,
                    "sub_queries_used": sub_queries_used,
                    "budget": budget,
                    "estimate": estimate,
                }

            store_name = step.get("store")
            if store_name:
                variables[store_name] = current

            step_trace["status"] = "ok"
            step_trace["preview"] = self._recipe_preview(current)
            trace.append(step_trace)
            await _report(float(step_index), total_steps, f"Step {step_index}/{int(total_steps)} ({op}) done")

        session.evidence.append(
            _Evidence(
                source="exec",
                line_range=None,
                pattern=None,
                note=f"run_recipe completed ({len(trace)} steps)",
                snippet=self._recipe_preview(current),
            )
        )

        payload = {
            "context_id": resolved_context_id,
            "recipe_version": normalized["version"],
            "step_count": len(normalized["steps"]),
            "sub_queries_used": sub_queries_used,
            "budget": budget,
            "estimate": estimate,
            "trace": trace,
            "value": _to_jsonable(current),
            "variables": sorted(variables.keys()),
        }
        return True, payload

    async def _compile_recipe_code(
        self,
        *,
        code: str,
        context_id: str = "default",
    ) -> tuple[bool, dict[str, Any]]:
        if context_id not in self._sessions:
            return False, {"error": f"No context loaded with ID '{context_id}'."}

        session = self._sessions[context_id]
        session.iterations += 1
        result = await session.repl.execute_async(code)
        if result.error:
            return False, {
                "error": f"Recipe code execution failed: {result.error}",
                "execution": {
                    "stderr": result.stderr,
                    "stdout": result.stdout,
                },
            }

        candidate = result.return_value
        if candidate is None:
            candidate = session.repl.get_variable("recipe")

        if candidate is None:
            return False, {
                "error": (
                    "Recipe code did not return a recipe value. "
                    "Return a RecipeBuilder/dict or assign to variable `recipe`."
                ),
            }

        compiled: Any = candidate
        if isinstance(candidate, dict):
            compiled = dict(candidate)
        elif hasattr(candidate, "compile") and callable(getattr(candidate, "compile")):
            compiled = candidate.compile()  # type: ignore[call-arg]
        elif hasattr(candidate, "to_dict") and callable(getattr(candidate, "to_dict")):
            compiled = candidate.to_dict()  # type: ignore[call-arg]
        else:
            return False, {
                "error": (
                    "Recipe code returned unsupported type. "
                    "Expected dict or object with compile()/to_dict()."
                ),
                "type": str(type(candidate)),
            }

        normalized, errors = _validate_recipe(compiled)
        if errors or normalized is None:
            return False, {
                "error": "Compiled recipe is invalid.",
                "errors": errors,
                "compiled": _to_jsonable(compiled),
            }

        return True, {
            "context_id": context_id,
            "recipe": normalized,
            "estimate": _estimate_recipe(normalized),
            "execution": {
                "variables_updated": result.variables_updated,
                "execution_time_ms": result.execution_time_ms,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        }

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
        def set_backend(backend: str) -> str:
            ok, message = self._apply_sub_query_runtime_config(sub_query_backend=backend)
            if not ok:
                raise ValueError(message)
            snapshot = self._get_sub_query_config_snapshot()
            return (
                "sub_query_backend set to "
                f"{snapshot['sub_query_backend']!r} "
                f"(resolved: {snapshot['sub_query_backend_resolved']!r})"
            )

        def get_config() -> dict[str, Any]:
            return self._get_sub_query_config_snapshot()

        session.repl.set_variable("set_backend", set_backend)
        session.repl.set_variable("get_config", get_config)

    def _inject_repl_sub_query(self, session: _Session, context_id: str) -> None:
        async def sub_query(prompt: str, context_slice: str | None = None) -> str:
            success, output, _truncated, _backend = await self._run_sub_query(
                prompt=prompt,
                context_slice=context_slice,
                context_id=context_id,
                backend="auto",
            )
            if not success:
                return f"[ERROR: sub_query failed: {output}]"
            return output

        session.repl.inject_sub_query(sub_query)

    def _inject_repl_sub_aleph(self, session: _Session, context_id: str) -> None:
        async def sub_aleph(query: str, context: ContextType | None = None) -> AlephResponse:
            context_slice: str | None
            if context is None:
                context_slice = None
            elif isinstance(context, str):
                context_slice = context
            else:
                context_slice = _coerce_context_to_text(context)
            response, _meta = await self._run_sub_aleph(
                query=query,
                context_slice=context_slice,
                context_id=context_id,
            )
            return response

        session.repl.inject_sub_aleph(sub_aleph)

    def _configure_session(
        self,
        session: _Session,
        context_id: str,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if loop is not None:
            session.repl.set_loop(loop)
        self._inject_repl_sub_query(session, context_id)
        self._inject_repl_sub_aleph(session, context_id)
        self._inject_repl_config_helpers(session)

    async def _ensure_remote_server(self, server_id: str) -> tuple[bool, str | _RemoteServerHandle]:
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
        line_desc = "1-based" if line_number_base == 1 else "0-based"
        msg = (
            f"Context loaded '{context_id}': {meta.size_chars:,} chars, "
            f"{meta.size_lines:,} lines, ~{meta.size_tokens_estimate:,} tokens "
            f"(line numbers {line_desc})."
        )
        if note:
            msg += f"\nNote: {note}"
        return msg

    def _create_session(
        self,
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
        self._configure_session(self._sessions[context_id], context_id, loop=asyncio.get_running_loop())
        return meta

    def _get_or_create_session(
        self,
        context_id: str,
        line_number_base: LineNumberBase | None = None,
    ) -> _Session:
        session = self._sessions.get(context_id)
        if session is not None:
            self._configure_session(session, context_id, loop=asyncio.get_running_loop())
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
        self._configure_session(session, context_id, loop=asyncio.get_running_loop())
        return session

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

    def _require_actions(self, confirm: bool) -> str | None:
        if not self.action_config.enabled:
            return "Actions are disabled. Start the server with `--enable-actions`."
        if self.action_config.require_confirmation and not confirm:
            return "Confirmation required. Re-run with confirm=true."
        return None

    async def _maybe_resolve_workspace_from_roots(self, ctx: "Context") -> None:
        """Try to resolve workspace root from MCP client roots (lazy, once)."""
        if self._mcp_roots_resolved or self.action_config.workspace_root_explicit:
            return
        self._mcp_roots_resolved = True
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
            try:
                await ctx.info(f"Workspace root resolved from MCP roots: {result}")
            except Exception:
                pass

    def _record_action(self, session: _Session | None, note: str, snippet: str) -> None:
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

    def _build_memory_pack_payload(
        self,
        *,
        include_ctx: bool = True,
    ) -> tuple[dict[str, Any], list[str]]:
        sessions_payload: list[dict[str, Any]] = []
        skipped: list[str] = []
        for sid, sess in self._sessions.items():
            try:
                sessions_payload.append(_session_to_payload(sid, sess, include_ctx=include_ctx))
            except Exception:
                skipped.append(sid)
        payload = {
            "schema": "aleph.memory_pack.v1",
            "created_at": datetime.now().isoformat(),
            "sessions": sessions_payload,
            "skipped": skipped,
        }
        return payload, skipped

    async def _run_subprocess(
        self,
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

    def _parse_rg_vimgrep(self, output: str, max_results: int) -> tuple[list[dict[str, Any]], bool]:
        results: list[dict[str, Any]] = []
        truncated = False
        limit = max_results if max_results > 0 else None
        for line in output.splitlines():
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            path_str, line_str, col_str, text = parts
            try:
                line_no = int(line_str)
                col_no = int(col_str)
            except ValueError:
                continue
            results.append({
                "path": path_str,
                "line": line_no,
                "column": col_no,
                "text": text,
            })
            if limit is not None and len(results) >= limit:
                truncated = True
                break
        return results, truncated

    def _python_rg_search(
        self,
        pattern: str,
        roots: list[Path],
        glob_pattern: str | None,
        max_results: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        results: list[dict[str, Any]] = []
        truncated = False
        limit = max_results if max_results > 0 else None
        rx = re.compile(pattern)
        skip_dirs = {".git", ".venv", "node_modules", "dist", "build", "__pycache__", ".mypy_cache", ".pytest_cache"}

        def _iter_files(root: Path) -> Iterable[Path]:
            if root.is_file():
                yield root
                return
            for path in root.rglob("*"):
                if path.is_dir():
                    continue
                if any(part in skip_dirs for part in path.parts):
                    continue
                yield path

        for root in roots:
            for path in _iter_files(root):
                if glob_pattern and not fnmatch.fnmatch(path.name, glob_pattern):
                    continue
                try:
                    if path.stat().st_size > self.action_config.max_read_bytes:
                        continue
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        for idx, line in enumerate(f, start=1):
                            match = rx.search(line)
                            if not match:
                                continue
                            results.append({
                                "path": str(path),
                                "line": idx,
                                "column": match.start() + 1,
                                "text": line.rstrip("\n"),
                            })
                            if limit is not None and len(results) >= limit:
                                truncated = True
                                return results, truncated
                except Exception:
                    continue
        return results, truncated

    def _auto_save_memory_pack(self) -> None:
        if self.context_policy == "isolated":
            return
        if not self.action_config.enabled or not self._sessions:
            return
        payload, _ = self._build_memory_pack_payload(include_ctx=True)
        out_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8", errors="replace")
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
        _tool = self._tool_decorator

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
            meta = self._create_session(text, context_id, fmt, base)
            return self._format_context_loaded(context_id, meta, base)

        @_tool()
        async def list_contexts(
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """List all active context sessions and their status."""
            items = []
            for cid, session in self._sessions.items():
                items.append({
                    "id": cid,
                    "chars": session.meta.size_chars,
                    "lines": session.meta.size_lines,
                    "iterations": session.iterations,
                    "evidence": len(session.evidence),
                })

            if output == "object":
                return {"count": len(items), "items": items}
            if output == "json":
                return json.dumps({"count": len(items), "items": items}, indent=2)

            res = [f"Found {len(items)} active context session(s):\n"]
            for item in items:
                res.append(f"- **{item['id']}**: {item['chars']:,} chars, {item['lines']:,} lines, {item['iterations']} iterations")
            return "\n".join(res)

        @_tool()
        async def diff_contexts(
            a: str,
            b: str,
            context_lines: int = 3,
            max_lines: int = 400,
            output: Literal["markdown", "text"] = "markdown",
        ) -> str:
            """Compare two context sessions using unified diff."""
            if a not in self._sessions:
                return f"Error: Context '{a}' not found."
            if b not in self._sessions:
                return f"Error: Context '{b}' not found."

            lines_a = str(self._sessions[a].repl.get_variable("ctx") or "").splitlines()
            lines_b = str(self._sessions[b].repl.get_variable("ctx") or "").splitlines()

            diff = list(difflib.unified_diff(
                lines_a, lines_b,
                fromfile=f"context:{a}",
                tofile=f"context:{b}",
                n=context_lines,
                lineterm=""
            ))

            if not diff:
                return f"Contexts '{a}' and '{b}' are identical."

            if len(diff) > max_lines:
                diff = diff[:max_lines] + ["... (diff truncated)"]

            diff_text = "\n".join(diff)
            if output == "markdown":
                rendered = f"### Diff: {a} vs {b}\n\n```diff\n{diff_text}\n```"
            else:
                rendered = diff_text

            text, _ = self._truncate_tool_text(rendered)
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
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if self.context_policy == "isolated" and not confirm:
                return _format_error(
                    "Isolated policy requires confirm=true for session export (prevents accidental context leaks).\n"
                    "To proceed: save_session(path=..., confirm=true)\n"
                    "To switch policy: configure(context_policy='trusted')",
                    output=output,
                )

            payload, skipped = self._build_memory_pack_payload()
            try:
                p = _scoped_path(self.action_config.workspace_root, path, self.action_config.workspace_mode)
            except Exception as e:
                return _format_error(f"Invalid path: {e}", output=output)

            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            except Exception as e:
                return _format_error(f"Failed to save: {e}", output=output)

            msg = f"Session saved to {path}."
            if skipped:
                msg += f" Warning: skipped {len(skipped)} sessions due to serialization errors."

            if output == "object":
                return {"status": "success", "path": str(p), "skipped": skipped}
            if output == "json":
                return json.dumps({"status": "success", "path": str(p), "skipped": skipped})
            return msg

        @_tool()
        async def load_session(
            path: str,
            context_id: str | None = None,
            session_id: str | None = None,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """Load session state from a file (Memory Pack)."""
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if self.context_policy == "isolated" and not confirm:
                return _format_error(
                    "Isolated policy requires confirm=true for session import (prevents unvetted context rehydration).\n"
                    "To proceed: load_session(path=..., confirm=true)\n"
                    "To switch policy: configure(context_policy='trusted')",
                    output=output,
                )

            try:
                p = _scoped_path(self.action_config.workspace_root, path, self.action_config.workspace_mode)
                with open(p, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                return _format_error(f"Failed to load: {e}", output=output)

            if payload.get("schema") != "aleph.memory_pack.v1":
                return _format_error("Invalid memory pack schema", output=output)

            loaded = []
            skipped: list[dict[str, str]] = []
            for sp in payload.get("sessions", []):
                sid = _resolve_session_payload_id(sp)
                if not sid:
                    skipped.append({"id": "<missing>", "error": "missing session identifier"})
                    continue
                try:
                    session = _session_from_payload(sp, sid, self.sandbox_config, asyncio.get_running_loop())
                    self._configure_session(session, sid, loop=asyncio.get_running_loop())
                    self._sessions[sid] = session
                    loaded.append(sid)
                except Exception as e:
                    skipped.append({"id": sid, "error": str(e)})

            msg = f"Loaded {len(loaded)} session(s) from {path}."
            if skipped:
                msg += f" Skipped {len(skipped)} invalid session(s)."
            if output == "object":
                return {"status": "success", "loaded": loaded, "skipped": skipped}
            if output == "json":
                return json.dumps({"status": "success", "loaded": loaded, "skipped": skipped})
            return msg

    def _register_action_tools(self) -> None:
        _tool = self._tool_decorator

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
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if ctx is not None:
                await self._maybe_resolve_workspace_from_roots(ctx)

            session = self._get_or_create_session(context_id)
            session.iterations += 1

            workspace_root = self.action_config.workspace_root
            cwd_path = (
                _scoped_path(workspace_root, cwd, self.action_config.workspace_mode)
                if cwd
                else workspace_root
            )
            timeout = timeout_seconds if timeout_seconds is not None else self.action_config.max_cmd_seconds

            if shell:
                user_shell = os.environ.get("SHELL", "/bin/sh")
                argv = [user_shell, "-lc", cmd]
            else:
                argv = shlex.split(cmd)
                if not argv:
                    return _format_error("Empty command", output=output)

            payload = await self._run_subprocess(argv=argv, cwd=cwd_path, timeout_seconds=timeout)
            session.repl._namespace["last_command_result"] = payload
            self._record_action(session, note="run_command", snippet=(payload.get("stdout") or payload.get("stderr") or "")[:200])
            return _format_payload(payload, output=output)

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
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if ctx is not None:
                await self._maybe_resolve_workspace_from_roots(ctx)
            if not pattern:
                return _format_error("pattern is required", output=output)
            if isinstance(paths, str):
                paths = [paths]

            session = self._get_or_create_session(context_id)
            session.iterations += 1

            workspace_root = self.action_config.workspace_root
            resolved_paths: list[Path] = []
            for p in paths or [str(workspace_root)]:
                try:
                    resolved = _scoped_path(workspace_root, p, self.action_config.workspace_mode)
                except Exception as e:
                    return _format_error(str(e), output=output)
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
                payload = await self._run_subprocess(
                    argv=argv,
                    cwd=workspace_root,
                    timeout_seconds=self.action_config.max_cmd_seconds,
                )
                matches, truncated = self._parse_rg_vimgrep(payload.get("stdout") or "", max_results)
            else:
                matches, truncated = self._python_rg_search(
                    pattern,
                    resolved_paths,
                    glob,
                    max_results,
                )

            hits_text = "\n".join(
                f"{m['path']}:{m['line']}:{m['column']}:{m['text']}" for m in matches
            )
            if load_context_id:
                meta = self._create_session(hits_text, load_context_id, ContentFormat.TEXT, DEFAULT_LINE_NUMBER_BASE)
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
            self._record_action(session, note="rg_search", snippet=f"{pattern} ({len(matches)} matches)")

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
                parts.extend([f"- {m['path']}:{m['line']}:{m['column']}: {m['text']}" for m in matches[:20]])
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
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if ctx is not None:
                await self._maybe_resolve_workspace_from_roots(ctx)

            base_override: LineNumberBase | None = None
            if line_number_base is not None:
                try:
                    base_override = _validate_line_number_base(line_number_base)
                except ValueError as e:
                    return _format_error(str(e), output=output)

            session = self._get_or_create_session(context_id, base_override)
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
                p = _scoped_path(self.action_config.workspace_root, path, self.action_config.workspace_mode)
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
            session.repl._namespace["last_read_file_result"] = payload
            self._record_action(session, note="read_file", snippet=f"{path} ({start_line}-{end_line})")
            return _format_payload(payload, output=output)

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
            err = self._require_actions(confirm)
            if err:
                return f"Error: {err}"
            if ctx is not None:
                await self._maybe_resolve_workspace_from_roots(ctx)

            try:
                base = _validate_line_number_base(line_number_base)
            except ValueError as e:
                return f"Error: {e}"

            try:
                p = _scoped_path(self.action_config.workspace_root, path, self.action_config.workspace_mode)
            except Exception as e:
                return f"Error: {e}"

            if not p.exists() or not p.is_file():
                return f"Error: File not found: {path}"

            try:
                text, detected_fmt, warning = _load_text_from_path(
                    p,
                    self.action_config.max_read_bytes,
                    self.action_config.max_cmd_seconds,
                )
            except ValueError as e:
                return f"Error: {e}"
            try:
                fmt = detected_fmt if format == "auto" else ContentFormat(format)
            except Exception as e:
                return f"Error: {e}"
            meta = self._create_session(text, context_id, fmt, base)
            session = self._get_or_create_session(context_id, base)
            self._record_action(session, note="load_file", snippet=str(p))
            return self._format_context_loaded(context_id, meta, base, note=warning)

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
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if ctx is not None:
                await self._maybe_resolve_workspace_from_roots(ctx)

            session = self._get_or_create_session(context_id)
            session.iterations += 1

            try:
                p = _scoped_path(self.action_config.workspace_root, path, self.action_config.workspace_mode)
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
            session.repl._namespace["last_write_file_result"] = payload
            self._record_action(session, note="write_file", snippet=f"{path} ({len(payload_bytes)} bytes)")
            return _format_payload(payload, output=output)

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
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if ctx is not None:
                await self._maybe_resolve_workspace_from_roots(ctx)

            session = self._get_or_create_session(context_id)
            session.iterations += 1

            workspace_root = self.action_config.workspace_root
            cwd_path = (
                _scoped_path(workspace_root, cwd, self.action_config.workspace_mode)
                if cwd
                else workspace_root
            )

            # Heuristics for test runner
            runner_bin: str = str(runner)
            if runner == "auto":
                runner_bin = "pytest"

            argv: list[str] = [runner_bin]
            if args:
                argv.extend(args)

            payload = await self._run_subprocess(argv=argv, cwd=cwd_path, timeout_seconds=self.action_config.max_cmd_seconds)
            self._record_action(session, note=f"run_tests: {runner}", snippet=(payload.get("stdout") or payload.get("stderr") or "")[:200])
            return _format_payload(payload, output=output)

    def _format_execution_result(self, result: ExecutionResult) -> str | dict[str, Any]:
        """Format sandboxed execution results for output."""
        if result.error:
            text, _ = self._truncate_tool_text(f"## Execution Error\n\n{result.error}")
            return text

        res = ["## Execution Result\n"]
        formatting_truncated = False
        if result.stdout:
            stdout_text, was_truncated = self._truncate_tool_text(result.stdout)
            formatting_truncated = formatting_truncated or was_truncated
            res.append(f"**Output:**\n```\n{stdout_text}\n```")
        if result.stderr:
            stderr_text, was_truncated = self._truncate_tool_text(result.stderr)
            formatting_truncated = formatting_truncated or was_truncated
            res.append(f"**Stderr:**\n```\n{stderr_text}\n```")
        if result.return_value is not None:
            rendered = repr(result.return_value)
            rendered, was_truncated = self._truncate_tool_text(rendered)
            formatting_truncated = formatting_truncated or was_truncated
            res.append(f"**Return Value:** `{rendered}`")
        if result.variables_updated:
            res.append(f"\n**Variables Updated:** {', '.join(f'`{v}`' for v in result.variables_updated)}")

        if result.truncated or formatting_truncated:
            res.append("\n*Note: Output was truncated*")

        out = "\n".join(res)
        out, _ = self._truncate_tool_text(out)
        return out

    def _truncate_tool_text(
        self,
        text: str,
        *,
        max_chars: int | None = None,
    ) -> tuple[str, bool]:
        limit = self.max_tool_response_chars if max_chars is None else max_chars
        if limit <= 0 or len(text) <= limit:
            return text, False
        if limit <= len(_TOOL_TRUNCATION_SUFFIX):
            return _TOOL_TRUNCATION_SUFFIX[:limit], True

        # Keep a compact prefix/suffix preview instead of a large contiguous head.
        # This avoids spilling big raw blocks (for example long repeated characters)
        # into the model context while still preserving enough signal for debugging.
        preview_each_side = min(400, max(0, (limit - len(_TOOL_TRUNCATION_SUFFIX)) // 2))
        if preview_each_side == 0:
            keep = limit - len(_TOOL_TRUNCATION_SUFFIX)
            return text[:keep] + _TOOL_TRUNCATION_SUFFIX, True
        return (
            text[:preview_each_side]
            + _TOOL_TRUNCATION_SUFFIX
            + text[-preview_each_side:]
        ), True

    def _limit_json_items(
        self,
        items: list[Any],
        *,
        max_chars: int | None = None,
    ) -> tuple[list[Any], bool]:
        limit = self.max_tool_response_chars if max_chars is None else max_chars
        used = 2  # [] delimiters
        limited: list[Any] = []

        for raw in items:
            item = _to_jsonable(raw)
            try:
                encoded = json.dumps(item, ensure_ascii=False)
            except Exception:
                encoded = json.dumps(str(item), ensure_ascii=False)

            projected = used + len(encoded) + (1 if limited else 0)
            if projected > limit:
                return limited, True

            limited.append(item)
            used = projected

        return limited, False

    def _format_variable_value(self, name: str, value: Any) -> Any:
        if value is None or isinstance(value, (int, float, bool)):
            return value

        if isinstance(value, str):
            text, truncated = self._truncate_tool_text(value)
            if not truncated:
                return value
            return {
                "name": name,
                "truncated": True,
                "original_chars": len(value),
                "value_preview": text,
            }

        jsonable = _to_jsonable(value)
        try:
            rendered = json.dumps(jsonable, ensure_ascii=False)
        except Exception:
            rendered = str(jsonable)
        text, truncated = self._truncate_tool_text(rendered)
        if not truncated:
            return jsonable
        return {
            "name": name,
            "truncated": True,
            "original_chars": len(rendered),
            "value_preview": text,
        }

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

    def _register_tools(self) -> None:
        """Register all MCP tools."""
        self._register_core_tools()
        self._register_action_tools()
        self._register_query_tools()
        self._register_reasoning_tools()
        self._register_mcp_tools()

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
        default_context_policy=DEFAULT_CONTEXT_POLICY,
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

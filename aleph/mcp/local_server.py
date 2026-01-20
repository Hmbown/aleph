"""Aleph MCP server for use with Claude Desktop, Cursor, Windsurf, etc.

This server exposes Aleph's context exploration tools and optional action tools.

Tools:
- load_context: Load text/data into sandboxed REPL
- peek_context: View character/line ranges
- search_context: Regex search with context
- semantic_search: Meaning-based search over the context
- exec_python: Execute Python code in sandbox
- get_variable: Retrieve variables from REPL
- sub_query: RLM-style recursive sub-agent queries (CLI or API backend)
- think: Structure a reasoning sub-step (returns prompt for YOU to reason about)
- tasks: Lightweight task tracking per context
- get_status: Show current session state
- get_evidence: Retrieve collected evidence/citations
- finalize: Mark task complete with answer
- chunk_context: Split context into chunks with metadata for navigation
- evaluate_progress: Self-evaluate progress with convergence tracking
- summarize_so_far: Compress reasoning history to manage context window
- rg_search: Fast repo search via ripgrep (action tool)

Usage:
    aleph
"""

from __future__ import annotations

import asyncio
import bz2
from contextlib import AsyncExitStack
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
from typing import Any, Iterable, Literal, cast
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from ..repl.sandbox import REPLEnvironment, SandboxConfig
from ..types import ContentFormat, ContextMetadata
from ..sub_query import SubQueryConfig, detect_backend, has_api_credentials
from ..sub_query.cli_backend import run_cli_sub_query, CLI_BACKENDS
from ..sub_query.api_backend import run_api_sub_query

__all__ = ["AlephMCPServerLocal", "main", "mcp"]


LineNumberBase = Literal[0, 1]
DEFAULT_LINE_NUMBER_BASE: LineNumberBase = 1
WorkspaceMode = Literal["fixed", "git", "any"]
DEFAULT_WORKSPACE_MODE: WorkspaceMode = "fixed"
ToolDocsMode = Literal["concise", "full"]
DEFAULT_TOOL_DOCS_MODE: ToolDocsMode = "concise"


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


DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS = _get_env_float(
    "ALEPH_REMOTE_TOOL_TIMEOUT",
    120.0,
)


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


def _coerce_context_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            return str(value)
    return str(value)


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
    # Lightweight task tracking
    tasks: list[dict[str, Any]] = field(default_factory=list)
    task_counter: int = 0


def _session_to_payload(session_id: str, session: _Session) -> dict[str, Any]:
    ctx_val = session.repl.get_variable("ctx")
    ctx_text = _coerce_context_to_text(ctx_val)
    tasks_payload: list[dict[str, Any]] = []
    for task in session.tasks:
        if isinstance(task, dict):
            tasks_payload.append(task)

    return {
        "schema": "aleph.session.v1",
        "session_id": session_id,
        "context_id": session_id,
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
        "ctx": ctx_text,
        "think_history": list(session.think_history),
        "confidence_history": list(session.confidence_history),
        "information_gain": list(session.information_gain),
        "chunks": session.chunks,
        "tasks": tasks_payload,
        "task_counter": session.task_counter,
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


def _session_from_payload(
    obj: dict[str, Any],
    resolved_id: str,
    sandbox_config: SandboxConfig,
    loop: asyncio.AbstractEventLoop | None,
) -> _Session:
    ctx = obj.get("ctx")
    if not isinstance(ctx, str):
        raise ValueError("Invalid session payload: ctx must be a string")

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
        config=sandbox_config,
        loop=loop,
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

    tasks_payload = obj.get("tasks")
    tasks: list[dict[str, Any]] = []
    if isinstance(tasks_payload, list):
        for task in tasks_payload:
            if not isinstance(task, dict):
                continue
            if "id" not in task or "title" not in task:
                continue
            tasks.append({
                "id": int(task.get("id")),
                "title": str(task.get("title")),
                "status": str(task.get("status") or "todo"),
                "note": task.get("note"),
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
            })

    task_counter = int(obj.get("task_counter") or (max((t["id"] for t in tasks), default=0)))

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
        tasks=tasks,
        task_counter=task_counter,
    )

    ev_list = obj.get("evidence")
    if isinstance(ev_list, list):
        for ev in ev_list:
            if not isinstance(ev, dict):
                continue
            source = ev.get("source")
            if source not in {"search", "peek", "exec", "manual", "action", "sub_query"}:
                continue
            line_range = ev.get("line_range")
            if isinstance(line_range, list) and len(line_range) == 2:
                try:
                    line_range = (int(line_range[0]), int(line_range[1]))
                except Exception:
                    line_range = None
            else:
                line_range = None
            timestamp = datetime.now()
            ts_str = ev.get("timestamp")
            if isinstance(ts_str, str):
                try:
                    timestamp = datetime.fromisoformat(ts_str)
                except Exception:
                    timestamp = datetime.now()
            session.evidence.append(
                _Evidence(
                    source=source,
                    line_range=line_range,
                    pattern=ev.get("pattern"),
                    snippet=str(ev.get("snippet") or ""),
                    note=ev.get("note"),
                    timestamp=timestamp,
                )
            )

    return session

def _resolve_env_dir() -> Path | None:
    """Return invocation directory from env vars, preferring explicit override."""

    def _resolve_path(value: str, base: Path | None) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (base or Path.cwd()) / path
        return path.resolve()

    def _resolve_invocation_dir() -> Path | None:
        for var in ("PWD", "INIT_CWD"):
            val = os.environ.get(var)
            if not val:
                continue
            path = _resolve_path(val, base=None)
            if path.is_dir():
                return path
        return None

    invocation_dir = _resolve_invocation_dir()
    override = os.environ.get("ALEPH_WORKSPACE_ROOT")
    if override:
        path = _resolve_path(override, base=invocation_dir)
        if path.is_dir():
            return path

    return invocation_dir


def _detect_workspace_root() -> Path:
    """Auto-detect workspace root with env override support.

    Detection order:
    1. ALEPH_WORKSPACE_ROOT env var (explicit override)
    2. PWD env var (invocation directory)
    3. INIT_CWD env var (npm/yarn invocation)
    4. os.getcwd() fallback
    5. Walk up to find .git from resolved directory
    """
    base = _resolve_env_dir() or Path.cwd()
    for parent in [base, *base.parents]:
        if (parent / ".git").exists():
            return parent
    return base


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
    workspace_mode: WorkspaceMode = DEFAULT_WORKSPACE_MODE
    require_confirmation: bool = False
    max_cmd_seconds: float = 60.0
    max_output_chars: int = 50_000
    max_read_bytes: int = 1_000_000_000   # Default 1GB. Increase if you have more RAM - the LLM only sees query results, not the file.
    max_write_bytes: int = 100_000_000    # 100 MB


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
    ) -> None:
        self.sandbox_config = sandbox_config or SandboxConfig()
        self.action_config = action_config or ActionConfig()
        self.sub_query_config = sub_query_config or SubQueryConfig()
        self.tool_docs_mode = tool_docs_mode
        self._sessions: dict[str, _Session] = {}
        self._remote_servers: dict[str, _RemoteServerHandle] = {}
        self._auto_pack_loaded = False
        self._streamable_http_task: asyncio.Task | None = None
        self._streamable_http_url: str | None = None
        self._streamable_http_host: str | None = None
        self._streamable_http_port: int | None = None
        self._streamable_http_path: str | None = None
        self._streamable_http_lock = asyncio.Lock()

        # Import MCP lazily so it's an optional dependency
        try:
            from mcp.server.fastmcp import FastMCP
        except Exception as e:
            raise RuntimeError(
                "MCP support requires the `mcp` package. Install with `pip install \"aleph-rlm[mcp]\"`."
            ) from e

        self.server = FastMCP("aleph-local")
        self._register_tools()

        if self.action_config.enabled:
            self._auto_load_memory_pack()

    def _auto_load_memory_pack(self) -> None:
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

    async def _reset_remote_server_handle(self, handle: _RemoteServerHandle) -> None:
        """Close and clear a remote server handle without removing registration."""
        if handle._stack is not None:
            try:
                await handle._stack.aclose()
            finally:
                handle._stack = None
                handle.session = None
                handle.connected_at = None
        else:
            handle.session = None
            handle.connected_at = None

    async def _close_remote_server(self, server_id: str) -> tuple[bool, str]:
        """Close a remote server connection and terminate the subprocess."""
        if server_id not in self._remote_servers:
            return False, f"Error: Remote server '{server_id}' not registered."

        handle = self._remote_servers[server_id]
        await self._reset_remote_server_handle(handle)
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
            await self._reset_remote_server_handle(handle)
            ok, res = await self._ensure_remote_server(server_id)
            if not ok:
                return False, f"Error: list_tools failed and reconnect failed: {res}"
            handle = res  # type: ignore[assignment]
            try:
                result = await handle.session.list_tools()  # type: ignore[union-attr]
                return True, _to_jsonable(result)
            except Exception as e2:
                return False, f"Error: list_tools failed after reconnect: {e2}"

    async def _remote_call_tool(
        self,
        server_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: float | None = DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS,
    ) -> tuple[bool, Any]:
        ok, res = await self._ensure_remote_server(server_id)
        if not ok:
            return False, res
        handle = res  # type: ignore[assignment]

        if not self._remote_tool_allowed(handle, tool):
            return False, f"Error: Tool '{tool}' is not allowed for remote server '{server_id}'."

        from datetime import timedelta

        read_timeout = timedelta(
            seconds=float(timeout_seconds or DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS)
        )
        try:
            result = await handle.session.call_tool(  # type: ignore[union-attr]
                name=tool,
                arguments=arguments or {},
                read_timeout_seconds=read_timeout,
            )
        except Exception as e:
            await self._reset_remote_server_handle(handle)
            ok, res = await self._ensure_remote_server(server_id)
            if not ok:
                return False, f"Error: call_tool failed and reconnect failed: {res}"
            handle = res  # type: ignore[assignment]
            try:
                result = await handle.session.call_tool(  # type: ignore[union-attr]
                    name=tool,
                    arguments=arguments or {},
                    read_timeout_seconds=read_timeout,
                )
            except Exception as e2:
                return False, f"Error: call_tool failed after reconnect: {e2}"

        result_jsonable = _to_jsonable(result)

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

        def _parse_rg_vimgrep(output: str, max_results: int) -> tuple[list[dict[str, Any]], bool]:
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
            pattern: str,
            roots: list[Path],
            glob: str | None,
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
                    if glob and not fnmatch.fnmatch(path.name, glob):
                        continue
                    try:
                        if path.stat().st_size > self.action_config.max_read_bytes:
                            continue
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for idx, line in enumerate(text.splitlines(), start=1):
                        match = rx.search(line)
                        if not match:
                            continue
                        results.append({
                            "path": str(path),
                            "line": idx,
                            "column": match.start() + 1,
                            "text": line,
                        })
                        if limit is not None and len(results) >= limit:
                            truncated = True
                            return results, truncated
            return results, truncated

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
                _record_action(sess, note="auto_save_memory_pack", snippet=str(p))

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
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            session = _get_or_create_session(context_id)
            session.iterations += 1

            workspace_root = self.action_config.workspace_root
            cwd_path = (
                _scoped_path(workspace_root, cwd, self.action_config.workspace_mode)
                if cwd
                else workspace_root
            )
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
            err = _require_actions(confirm)
            if err:
                return _format_error(err, output=output)
            if not pattern:
                return _format_error("pattern is required", output=output)

            session = _get_or_create_session(context_id)
            session.iterations += 1

            workspace_root = self.action_config.workspace_root
            resolved_paths: list[Path] = []
            for p in paths or [str(workspace_root)]:
                try:
                    resolved_paths.append(
                        _scoped_path(
                            workspace_root,
                            p,
                            self.action_config.workspace_mode,
                        )
                    )
                except Exception as e:
                    return _format_error(str(e), output=output)

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
                payload = await _run_subprocess(argv=argv, cwd=workspace_root, timeout_seconds=self.action_config.max_cmd_seconds)
                matches, truncated = _parse_rg_vimgrep(payload.get("stdout") or "", max_results)
            else:
                matches, truncated = _python_rg_search(pattern, resolved_paths, glob, max_results)

            hits_text = "\n".join(
                f"{m['path']}:{m['line']}:{m['column']}:{m['text']}" for m in matches
            )
            if load_context_id:
                meta = _create_session(hits_text, load_context_id, ContentFormat.TEXT, DEFAULT_LINE_NUMBER_BASE)
                session.repl._namespace["last_rg_loaded_context"] = load_context_id
                load_note = f"Loaded {len(matches)} match(es) into '{load_context_id}'."
            else:
                meta = None
                load_note = None

            result_payload = {
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
            _record_action(session, note="rg_search", snippet=f"{pattern} ({len(matches)} matches)")

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
            err = _require_actions(confirm)
            if err:
                return f"Error: {err}"

            try:
                base = _validate_line_number_base(line_number_base)
            except ValueError as e:
                return f"Error: {e}"

            try:
                p = _scoped_path(
                    self.action_config.workspace_root,
                    path,
                    self.action_config.workspace_mode,
                )
            except Exception as e:
                return f"Error: {e}"

            if not p.exists() or not p.is_file():
                return f"Error: File not found: {path}"

            try:
                text, detected_fmt, warning = _load_text_from_path(
                    p,
                    max_bytes=self.action_config.max_read_bytes,
                    timeout_seconds=self.action_config.max_cmd_seconds,
                )
            except ValueError as e:
                return f"Error: {e}"
            try:
                fmt = detected_fmt if format == "auto" else ContentFormat(format)
            except Exception as e:
                return f"Error: {e}"
            meta = _create_session(text, context_id, fmt, base)
            session = self._sessions[context_id]
            _record_action(session, note="load_file", snippet=str(p))
            return _format_context_loaded(context_id, meta, base, note=warning)

        @_tool()
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
                p = _scoped_path(
                    self.action_config.workspace_root,
                    path,
                    self.action_config.workspace_mode,
                )
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

        @_tool()
        async def run_tests(
            runner: Literal["auto", "pytest"] = "auto",
            args: list[str] | None = None,
            cwd: str | None = None,
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

            cwd_path = self.action_config.workspace_root
            if cwd:
                try:
                    cwd_path = _scoped_path(
                        self.action_config.workspace_root,
                        cwd,
                        self.action_config.workspace_mode,
                    )
                except Exception as e:
                    return _format_error(str(e), output=output)

            proc_payload = await _run_subprocess(
                argv=argv,
                cwd=cwd_path,
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

            _record_action(sa, note="diff_contexts", snippet=f"{a} vs {b}")
            _record_action(sb, note="diff_contexts", snippet=f"{a} vs {b}")

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
            err = _require_actions(confirm)
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
                    _record_action(sess, note="save_memory_pack", snippet=str(p))

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

            _record_action(session, note="save_session", snippet=str(p))
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
            err = _require_actions(confirm)
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
                    _record_action(session, note="load_memory_pack", snippet=str(p))
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
            _record_action(session, note="load_session", snippet=str(p))
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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

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
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

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
                task = {
                    "id": session.task_counter,
                    "title": title,
                    "status": status if status in valid_statuses else "todo",
                    "note": note,
                    "created_at": now,
                    "updated_at": now,
                }
                session.tasks.append(task)
            elif action in {"update", "done"}:
                if task_id is None:
                    return _format_error("task_id is required for update/done", output=output)
                task = next((t for t in session.tasks if t.get("id") == task_id), None)
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
            payload = {
                "context_id": context_id,
                "total": len(session.tasks),
                "counts": counts,
                "items": sorted(session.tasks, key=lambda t: int(t.get("id", 0))),
            }

            if output == "object":
                return payload
            if output == "json":
                return json.dumps(payload, ensure_ascii=False, indent=2)

            parts = [
                "## Tasks",
                f"Total: {payload['total']} (todo: {counts['todo']}, doing: {counts['doing']}, done: {counts['done']})",
            ]
            if payload["items"]:
                parts.append("")
                for task in payload["items"]:
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
            1. API - if ALEPH_SUB_QUERY_API_KEY or OPENAI_API_KEY is set (most reliable)
            2. codex CLI - if installed
            3. gemini CLI - if installed
            4. claude CLI - if installed (deprioritized: hangs in MCP/sandbox contexts)

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

        @_tool()
        async def call_remote_tool(
            server_id: str,
            tool: str,
            arguments: dict[str, Any] | None = None,
            timeout_seconds: float | None = DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Call a tool on a remote MCP server.

            Args:
                server_id: Registered remote server ID
                tool: Tool name
                arguments: Tool arguments object
                timeout_seconds: Tool call timeout (best-effort). Defaults to ALEPH_REMOTE_TOOL_TIMEOUT or 120s.
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

        @_tool()
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

    async def run(self, transport: str = "stdio") -> None:
        """Run the MCP server."""
        if transport != "stdio":
            raise ValueError("Only stdio transport is supported")

        await self.server.run_stdio_async()


_mcp_instance: Any | None = None


def _get_mcp_instance() -> Any:
    global _mcp_instance
    if _mcp_instance is None:
        _mcp_instance = AlephMCPServerLocal().server
    return _mcp_instance


def __getattr__(name: str) -> Any:
    if name == "mcp":
        return _get_mcp_instance()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    """CLI entry point: `aleph` or `python -m aleph.mcp.local_server`"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Aleph as an MCP server for local AI reasoning"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Code execution timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--max-output",
        type=int,
        default=50000,
        help="Maximum output characters (default: 50000)",
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
        help="Workspace root for action tools (default: ALEPH_WORKSPACE_ROOT env or auto-detect from PWD/INIT_CWD/cwd)",
    )
    parser.add_argument(
        "--workspace-mode",
        type=str,
        choices=["fixed", "git", "any"],
        default=DEFAULT_WORKSPACE_MODE,
        help="Path scope for action tools: fixed (workspace root only), git (any git repo), any (no path restriction)",
    )
    parser.add_argument(
        "--require-confirmation",
        action="store_true",
        help="Require confirm=true for action tools",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=1_000_000_000,
        help="Max file size in bytes for load_file/read_file (default: 1GB). Increase based on your RAM—the LLM only sees query results.",
    )
    parser.add_argument(
        "--max-write-bytes",
        type=int,
        default=100_000_000,
        help="Max file size in bytes for write_file/save_session (default: 100MB).",
    )
    env_tool_docs = os.environ.get("ALEPH_TOOL_DOCS")
    default_tool_docs = env_tool_docs if env_tool_docs in {"concise", "full"} else DEFAULT_TOOL_DOCS_MODE
    parser.add_argument(
        "--tool-docs",
        type=str,
        choices=["concise", "full"],
        default=default_tool_docs,
        help="Tool description verbosity for MCP clients: concise (default) or full",
    )

    args = parser.parse_args()

    config = SandboxConfig(
        timeout_seconds=args.timeout,
        max_output_chars=args.max_output,
    )

    action_cfg = ActionConfig(
        enabled=bool(args.enable_actions),
        workspace_root=Path(args.workspace_root).resolve() if args.workspace_root else _detect_workspace_root(),
        workspace_mode=cast(WorkspaceMode, args.workspace_mode),
        require_confirmation=bool(args.require_confirmation),
        max_read_bytes=args.max_file_size,
        max_write_bytes=args.max_write_bytes,
    )

    server = AlephMCPServerLocal(
        sandbox_config=config,
        action_config=action_cfg,
        tool_docs_mode=cast(ToolDocsMode, args.tool_docs),
    )
    asyncio.run(server.run())


if __name__ == "__main__":
    main()

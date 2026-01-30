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
- sub_aleph: RLM recursion via nested Aleph calls
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
from collections import OrderedDict
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

from ..config import AlephConfig
from ..core import Aleph
from ..prompts.system import DEFAULT_SYSTEM_PROMPT
from ..providers.registry import get_provider
from ..repl.sandbox import REPLEnvironment, SandboxConfig
from ..types import AlephResponse, Budget, ContentFormat, ContextMetadata, ContextType, ExecutionResult
from ..sub_query import SubQueryConfig, detect_backend
from ..sub_query.cli_backend import run_cli_sub_query, CLI_BACKENDS
from ..sub_query.api_backend import run_api_sub_query

__all__ = ["AlephMCPServerLocal", "main", "mcp"]

mcp: Any


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
    source: Literal["search", "peek", "exec", "manual", "action", "sub_query", "sub_aleph"]
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
        context_preview=meta.sample_preview,
        structure_hint=meta.structure_hint or "N/A",
    )
    instructions = (
        "SINGLE-SHOT MODE (no live Python REPL in this call):\n"
        "- Do not output code blocks.\n"
        "- Answer directly and wrap the final answer in FINAL(...).\n"
    )
    return f"{system_prompt}\n\n{instructions}\nQUERY:\n{query}"


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
    # Recursion depth tracking for sub_aleph
    max_depth_seen: int = 1


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
    raw_line_number_base = obj.get("line_number_base")
    if isinstance(raw_line_number_base, (int, str)):
        line_number_base_val = raw_line_number_base
    else:
        line_number_base_val = 0
    try:
        base = _validate_line_number_base(int(line_number_base_val))
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
            raw_id = task.get("id")
            if raw_id is None:
                continue
            try:
                task_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            tasks.append({
                "id": task_id,
                "title": str(task.get("title")),
                "status": str(task.get("status") or "todo"),
                "note": task.get("note"),
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
            })

    raw_task_counter = obj.get("task_counter")
    if isinstance(raw_task_counter, (int, str)):
        try:
            task_counter = int(raw_task_counter)
        except (TypeError, ValueError):
            task_counter = max((t["id"] for t in tasks), default=0)
    else:
        task_counter = max((t["id"] for t in tasks), default=0)

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
            if source not in {"search", "peek", "exec", "manual", "action", "sub_query", "sub_aleph"}:
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

        if context_slice is None and session:
            ctx_val = session.repl.get_variable("ctx")
            if ctx_val is not None:
                context_slice = _coerce_context_to_text(ctx_val)

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

        try:
            while True:
                if resolved_backend in CLI_BACKENDS:
                    mcp_server_url = None
                    server_name = "aleph_shared"
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
                            return False, f"Failed to start streamable HTTP server: {url_or_err}", truncated, resolved_backend
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
                        backend=resolved_backend,  # type: ignore[arg-type]
                        timeout=self.sub_query_config.cli_timeout_seconds,
                        cwd=self.action_config.workspace_root if self.action_config.enabled else None,
                        max_output_chars=self.sub_query_config.cli_max_output_chars,
                        mcp_server_url=mcp_server_url,
                        mcp_server_name=server_name,
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
                    prompt = (
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

        if context_slice is None and session:
            ctx_val = session.repl.get_variable("ctx")
            if ctx_val is not None:
                context_slice = _coerce_context_to_text(ctx_val)

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
                    success, output = await run_cli_sub_query(
                        prompt=prompt,
                        context_slice=cli_context if cli_context else None,
                        backend=resolved_backend,  # type: ignore[arg-type]
                        timeout=self.sub_query_config.cli_timeout_seconds,
                        cwd=self.action_config.workspace_root if self.action_config.enabled else None,
                        max_output_chars=self.sub_query_config.cli_max_output_chars,
                        mcp_server_url=mcp_server_url,
                        mcp_server_name=server_name,
                        trust_mcp_server=True,
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

    def _get_sub_query_config_snapshot(self) -> dict[str, Any]:
        backend_env = os.environ.get("ALEPH_SUB_QUERY_BACKEND", "").strip().lower() or "auto"
        return {
            "sub_query_backend": backend_env,
            "sub_query_backend_resolved": detect_backend(self.sub_query_config),
            "sub_query_timeout_seconds": {
                "cli": self.sub_query_config.cli_timeout_seconds,
                "api": self.sub_query_config.api_timeout_seconds,
            },
            "sub_query_share_session": _get_env_bool("ALEPH_SUB_QUERY_SHARE_SESSION", False),
        }

    def _apply_sub_query_runtime_config(
        self,
        *,
        sub_query_backend: str | None = None,
        sub_query_timeout: float | None = None,
        sub_query_share_session: bool | None = None,
    ) -> tuple[bool, str]:
        allowed_backends = {"auto", "api", *CLI_BACKENDS}

        if sub_query_backend is not None:
            backend = sub_query_backend.strip().lower()
            if backend not in allowed_backends:
                allowed_list = ", ".join(sorted(allowed_backends))
                return False, f"Unsupported backend '{sub_query_backend}'. Choose from: {allowed_list}."
            os.environ["ALEPH_SUB_QUERY_BACKEND"] = backend
            self.sub_query_config.backend = backend  # type: ignore[assignment]

        if sub_query_timeout is not None:
            if sub_query_timeout <= 0:
                return False, "sub_query_timeout must be greater than 0."
            self.sub_query_config.cli_timeout_seconds = sub_query_timeout
            self.sub_query_config.api_timeout_seconds = sub_query_timeout
            os.environ["ALEPH_SUB_QUERY_TIMEOUT"] = str(sub_query_timeout)

        if sub_query_share_session is not None:
            os.environ["ALEPH_SUB_QUERY_SHARE_SESSION"] = (
                "true" if sub_query_share_session else "false"
            )

        return True, "Configuration updated."

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
        if not isinstance(res, _RemoteServerHandle):
            return False, res
        handle = res
        session = handle.session
        if session is None:
            return False, f"Error: Remote server '{server_id}' is not connected."
        try:
            result = await session.list_tools()
            return True, _to_jsonable(result)
        except Exception:
            await self._reset_remote_server_handle(handle)
            ok, res = await self._ensure_remote_server(server_id)
            if not ok:
                return False, f"Error: list_tools failed and reconnect failed: {res}"
            if not isinstance(res, _RemoteServerHandle):
                return False, res
            handle = res
            session = handle.session
            if session is None:
                return False, f"Error: Remote server '{server_id}' is not connected."
            try:
                result = await session.list_tools()
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
        if not isinstance(res, _RemoteServerHandle):
            return False, res
        handle = res

        if not self._remote_tool_allowed(handle, tool):
            return False, f"Error: Tool '{tool}' is not allowed for remote server '{server_id}'."

        from datetime import timedelta

        read_timeout = timedelta(
            seconds=float(timeout_seconds or DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS)
        )
        session = handle.session
        if session is None:
            return False, f"Error: Remote server '{server_id}' is not connected."
        try:
            result = await session.call_tool(
                name=tool,
                arguments=arguments or {},
                read_timeout_seconds=read_timeout,
            )
        except Exception:
            await self._reset_remote_server_handle(handle)
            ok, res = await self._ensure_remote_server(server_id)
            if not ok:
                return False, f"Error: call_tool failed and reconnect failed: {res}"
            if not isinstance(res, _RemoteServerHandle):
                return False, res
            handle = res
            session = handle.session
            if session is None:
                return False, f"Error: Remote server '{server_id}' is not connected."
            try:
                result = await session.call_tool(
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

    def _build_memory_pack_payload(self) -> tuple[dict[str, Any], list[str]]:
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
        if not self.action_config.enabled or not self._sessions:
            return
        payload, _ = self._build_memory_pack_payload()
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
                return f"### Diff: {a} vs {b}\n\n```diff\n{diff_text}\n```"
            return diff_text

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

            try:
                p = _scoped_path(self.action_config.workspace_root, path, self.action_config.workspace_mode)
                with open(p, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                return _format_error(f"Failed to load: {e}", output=output)

            if payload.get("schema") != "aleph.memory_pack.v1":
                return _format_error("Invalid memory pack schema", output=output)

            loaded = []
            for sp in payload.get("sessions", []):
                sid = sp.get("id")
                if sid:
                    try:
                        self._sessions[sid] = _session_from_payload(sp, sid, self.sandbox_config, asyncio.get_running_loop())
                        self._configure_session(self._sessions[sid], sid, loop=asyncio.get_running_loop())
                        loaded.append(sid)
                    except Exception:
                        pass

            msg = f"Loaded {len(loaded)} session(s) from {path}."
            if output == "object":
                return {"status": "success", "loaded": loaded}
            if output == "json":
                return json.dumps({"status": "success", "loaded": loaded})
            return msg

    def _register_action_tools(self) -> None:
        _tool = self._tool_decorator

        @_tool()
        async def run_tests(
            runner: Literal["auto", "pytest"] = "auto",
            args: list[str] | None = None,
            cwd: str | None = None,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
            context_id: str = "default",
        ) -> str | dict[str, Any]:
            """Run project tests."""
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)

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
            return f"## Execution Error\n\n{result.error}"

        res = ["## Execution Result\n"]
        if result.stdout:
            res.append(f"**Output:**\n```\n{result.stdout}\n```")
        if result.stderr:
            res.append(f"**Stderr:**\n```\n{result.stderr}\n```")
        if result.return_value is not None:
            res.append(f"**Return Value:** `{result.return_value!r}`")
        if result.variables_updated:
            res.append(f"\n**Variables Updated:** {', '.join(f'`{v}`' for v in result.variables_updated)}")

        if result.truncated:
            res.append("\n*Note: Output was truncated*")

        return "\n".join(res)

    def _register_query_tools(self) -> None:
        _tool = self._tool_decorator

        @_tool()
        async def peek_context(
            start: int = 0,
            end: int | None = None,
            unit: Literal["chars", "lines"] = "chars",
            record_evidence: bool = False,
            context_id: str = "default",
        ) -> str:
            """View a portion of the loaded context."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

            fn = repl.get_variable("peek") if unit == "chars" else repl.get_variable("lines")
            if not callable(fn):
                return f"Error: {unit} helper is not available"

            try:
                res = fn(start, end)
            except Exception as e:
                return f"Error: {e}"

            if record_evidence and res:
                line_range = None
                if unit == "lines":
                    line_range = (start, end if end is not None else session.meta.size_lines)
                session.evidence.append(
                    _Evidence(
                        source="peek",
                        line_range=line_range,
                        pattern=None,
                        note=f"peek {unit} {start}:{end}",
                        snippet=str(res)[:200],
                    )
                )

            return str(res)

        @_tool()
        async def search_context(
            pattern: str,
            context_id: str = "default",
            context_lines: int = 2,
            max_results: int = 10,
            record_evidence: bool = True,
            evidence_mode: Literal["summary", "all"] = "summary",
        ) -> str:
            """Search the context using regex patterns."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

            fn = repl.get_variable("search")
            if not callable(fn):
                return "Error: search() helper is not available"

            try:
                results = fn(pattern, context_lines=context_lines, max_results=max_results)
            except Exception as e:
                return f"Error: {e}"

            if not isinstance(results, list):
                return f"Error: search() returned unexpected type {type(results)}"

            if record_evidence and results:
                if evidence_mode == "summary":
                    session.evidence.append(
                        _Evidence(
                            source="search",
                            line_range=None,
                            pattern=pattern,
                            note=f"{len(results)} match(es) (summary)",
                            snippet=str(results[0].get("match", ""))[:200] if results else "",
                        )
                    )
                else:
                    for r in results:
                        if isinstance(r, dict):
                            line_no = int(r.get("line_num", 0))
                            session.evidence.append(
                                _Evidence(
                                    source="search",
                                    line_range=(line_no, line_no),
                                    pattern=pattern,
                                    note="match",
                                    snippet=str(r.get("match", ""))[:200],
                                )
                            )

            if not results:
                return f"No matches found for `{pattern}`."

            res = [f"## Search Results for `{pattern}`\n"]
            res.append(f"Found {len(results)} match(es) (line numbers are {session.line_number_base}-based):\n")
            for r in results:
                if isinstance(r, dict):
                    res.append(f"**Line {r.get('line_num')}:**")
                    res.append(f"```\n{r.get('context')}\n```")
            return "\n".join(res)

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
            """Semantic search over the context using lightweight embeddings."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

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

            if not isinstance(results, list):
                return f"Error: semantic_search() returned unexpected type {type(results)}"

            if record_evidence and results:
                session.evidence.append(
                    _Evidence(
                        source="search",
                        line_range=None,
                        pattern=None,
                        note=f"semantic search: {query}",
                        snippet=str(results[0].get("text", ""))[:200] if results else "",
                    )
                )

            if output == "object":
                return {"results": results}
            if output == "json":
                return json.dumps({"results": results}, indent=2)

            if not results:
                return f"No semantic matches found for `{query}`."

            res = [f"## Semantic Results for `{query}`\n"]
            for r in results:
                if isinstance(r, dict):
                    score = r.get("score", 0.0)
                    text = r.get("text", "")
                    res.append(f"### Score: {score:.4f}")
                    res.append(f"```\n{text}\n```")
            return "\n".join(res)

        @_tool()
        async def exec_python(
            code: str,
            context_id: str = "default",
        ) -> str | dict[str, Any]:
            """Execute Python code in the sandboxed REPL."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            repl = session.repl
            session.iterations += 1

            try:
                result = await repl.execute_async(code)
            except Exception as e:
                return f"Error: {e}"

            return self._format_execution_result(result)

        @_tool()
        async def get_variable(
            name: str,
            context_id: str = "default",
        ) -> Any:
            """Retrieve a variable from the REPL namespace."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            return session.repl.get_variable(name)

    def _register_reasoning_tools(self) -> None:
        _tool = self._tool_decorator

        @_tool()
        async def think(
            question: str,
            context_slice: str | None = None,
            context_id: str = "default",
        ) -> str:
            """Structure a reasoning sub-step.

            Use this to state your plan, record an observation, or ask a sub-question
            before taking an action. This helps structure the loop.

            Args:
                question: The reasoning step, observation, or sub-question
                context_slice: Optional snippet of context relevant to this step
                context_id: Context identifier
            """
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            session.iterations += 1

            # Log to internal reasoning trace
            log_entry = {
                "iteration": session.iterations,
                "question": question,
                "context_slice": context_slice[:200] if context_slice else None,
                "timestamp": datetime.now().isoformat(),
            }
            session.repl._namespace.setdefault("_reasoning_trace", []).append(log_entry)  # type: ignore

            res = [
                "## Reasoning Step",
                "",
                f"**Question:** {question}",
            ]
            if context_slice:
                res.append(f"\n---\n\n**Relevant Context:**\n```\n{context_slice}\n```")

            res.append("\n---\n\n**Your task:** Reason through this step-by-step. Consider:")
            res.append("1. What information do you have?")
            res.append("2. What can you infer?")
            res.append("3. What's the answer to this sub-question?")
            res.append("\n*After reasoning, use `exec_python` to verify or `finalize` if done.*")

            return "\n".join(res)

        @_tool()
        async def tasks(
            action: Literal["list", "add", "update", "clear"] = "list",
            task_id: str | None = None,
            description: str | None = None,
            status: Literal["todo", "done", "blocked"] = "todo",
            context_id: str = "default",
        ) -> str | dict[str, Any]:
            """Track tasks attached to a context."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            session.iterations += 1
            tasks_list: list[dict[str, Any]] = session.repl._namespace.setdefault("_tasks", [])  # type: ignore

            if action == "add" and description:
                new_id = task_id or f"T{len(tasks_list) + 1}"
                tasks_list.append({"id": new_id, "description": description, "status": status})
                return f"Task {new_id} added."

            if action == "update" and task_id:
                for t in tasks_list:
                    if t["id"] == task_id:
                        if description:
                            t["description"] = description
                        t["status"] = status
                        return f"Task {task_id} updated."
                return f"Error: Task {task_id} not found."

            if action == "clear":
                session.repl._namespace["_tasks"] = []
                return "All tasks cleared."

            # Default: list
            if not tasks_list:
                return "No tasks tracked for this context."

            res = ["## Task List\n"]
            for t in tasks_list:
                icon = "✅" if t["status"] == "done" else "⏳" if t["status"] == "todo" else "🚫"
                res.append(f"- {icon} **{t['id']}**: {t['description']}")
            return "\n".join(res)

        @_tool()
        async def get_status(
            context_id: str = "default",
            output: Literal["markdown", "json", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Session state."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            tasks_list: list[dict[str, Any]] = session.repl._namespace.get("_tasks", [])  # type: ignore
            status = {
                "context_id": context_id,
                "iterations": session.iterations,
                "evidence_count": len(session.evidence),
                "tasks_count": len(tasks_list),
                "variables": [k for k in session.repl._namespace.keys() if not k.startswith("_")],
                "size_chars": session.meta.size_chars,
                "size_lines": session.meta.size_lines,
            }

            if output == "object":
                return status
            if output == "json":
                return json.dumps(status, indent=2)

            res = [f"## Session Status: {context_id}\n"]
            res.append(f"- **Iterations**: {session.iterations}")
            res.append(f"- **Evidence Items**: {len(session.evidence)}")
            res.append(f"- **Tracked Tasks**: {len(session.repl._namespace.get('_tasks', []))}")  # type: ignore
            res.append(f"- **User Variables**: {', '.join(status['variables']) or 'None'}")  # type: ignore
            res.append(f"- **Context Size**: {session.meta.size_chars:,} chars ({session.meta.size_lines:,} lines)")
            return "\n".join(res)

        @_tool()
        async def get_evidence(
            limit: int = 20,
            offset: int = 0,
            source: Literal["any", "search", "peek", "exec", "manual", "action"] = "any",
            context_id: str = "default",
            output: Literal["markdown", "json", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Retrieve collected evidence/citations for a session."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            filtered = session.evidence
            if source != "any":
                filtered = [e for e in filtered if e.source == source]

            count = len(filtered)
            window = filtered[offset : offset + limit]

            items = []
            for ev in window:
                items.append({
                    "source": ev.source,
                    "line_range": ev.line_range,
                    "pattern": ev.pattern,
                    "note": ev.note,
                    "snippet": ev.snippet,
                })

            if output == "object":
                return {"total": count, "items": items}
            if output == "json":
                return json.dumps({"total": count, "items": items}, indent=2)

            if not items:
                return "No evidence found matching criteria."

            res = [f"## Evidence Log (Total: {count})\n"]
            for i, item in enumerate(items, offset + 1):
                source_info = f"[{item['source']}]"
                lr = item["line_range"]
                if isinstance(lr, (list, tuple)) and len(lr) >= 2:
                    source_info += f" lines {lr[0]}-{lr[1]}"
                if item["pattern"]:
                    source_info += f" pattern: `{item['pattern']}`"
                if item["note"]:
                    source_info += f" note: {item['note']}"
                res.append(f"{i}. {source_info}: \"{item['snippet'][:100]}...\"")  # type: ignore
            return "\n".join(res)

        @_tool()
        async def finalize(
            answer: str,
            confidence: Literal["high", "medium", "low"] = "medium",
            reasoning_summary: str | None = None,
            context_id: str = "default",
        ) -> str:
            """Mark the task complete with your final answer."""
            parts = ["## Final Answer", "", answer]
            if reasoning_summary:
                parts.extend(["", "---", "", f"**Reasoning:** {reasoning_summary}"])

            if context_id in self._sessions:
                session = self._sessions[context_id]
                parts.extend(["", f"*Completed after {session.iterations} iterations.*"])

            parts.append(f"\n**Confidence:** {confidence}")

            if context_id in self._sessions:
                session = self._sessions[context_id]
                if session.evidence:
                    parts.extend(["", "---", "", "### Evidence Citations"])
                    parts.append(f"*Line numbers are {'1-based' if session.line_number_base == 1 else '0-based'}.*")
                    for i, ev in enumerate(session.evidence[-10:], 1):
                        source_info = f"[{ev.source}]"
                        if ev.line_range:
                            source_info += f" lines {ev.line_range[0]}-{ev.line_range[1]}"
                        if ev.pattern:
                            source_info += f" pattern: `{ev.pattern}`"
                        if ev.note:
                            source_info += f" note: {ev.note}"
                        parts.append(f"{i}. {source_info}: \"{ev.snippet[:80]}...\"" if len(ev.snippet) > 80 else f"{i}. {source_info}: \"{ev.snippet}\"")

            self._auto_save_memory_pack()
            return "\n".join(parts)

        async def sub_query(
            prompt: str,
            context_slice: str | None = None,
            context_id: str = "default",
            backend: str = "auto",
            format: Literal["markdown", "raw"] = "markdown",
            validation_regex: str | None = None,
            max_retries: int | None = None,
            retry_prompt: str | None = None,
        ) -> str:
            """Run a sub-query using a spawned sub-agent."""
            success, output, _truncated, _backend = await self._run_sub_query(
                prompt=prompt,
                context_slice=context_slice,
                context_id=context_id,
                backend=backend,
                validation_regex=validation_regex,
                max_retries=max_retries,
                retry_prompt=retry_prompt,
            )
            if not success:
                return f"Error: sub_query failed: {output}"
            return output

        @_tool()
        async def sub_aleph(
            query: str,
            context_slice: str | None = None,
            context_id: str = "default",
            max_tokens: int | None = None,
            max_depth: int | None = None,
        ) -> str:
            """Recursively solve a sub-problem using Aleph."""
            response, _meta = await self._run_sub_aleph(
                query=query,
                context_slice=context_slice,
                context_id=context_id,
                max_tokens=max_tokens,
                max_depth=max_depth,
            )
            return response.answer

        @_tool()
        async def evaluate_progress(
            current_understanding: str,
            remaining_questions: list[str] | str | None = None,
            confidence_score: float = 0.5,
            context_id: str = "default",
        ) -> str:
            """Self-evaluate your progress."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            session.iterations += 1

            res = ["## Progress Evaluation", "", f"**Current Understanding:** {current_understanding}", f"\n**Confidence Score:** {confidence_score:.2f}"]
            if remaining_questions:
                res.append("\n**Remaining Questions:**")
                if isinstance(remaining_questions, str):
                    res.append(f"- {remaining_questions}")
                else:
                    for q in remaining_questions:
                        res.append(f"- {q}")

            if confidence_score > 0.9:
                res.append("\n*Confidence is high. Consider finalizing if the goal is met.*")
            elif confidence_score < 0.3:
                res.append("\n*Confidence is low. Try a different search pattern or tool.*")

            return "\n".join(res)

        @_tool()
        async def summarize_so_far(
            context_id: str = "default",
            include_evidence: bool = True,
            include_variables: bool = True,
            clear_history: bool = False,
        ) -> str:
            """Compress reasoning history to manage context window."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'."

            session = self._sessions[context_id]
            session.iterations += 1

            summary = f"Session '{context_id}' has run for {session.iterations} iterations."
            summary += f" Context size: {session.meta.size_chars:,} chars."

            if include_evidence and session.evidence:
                summary += f"\nEvidence collected: {len(session.evidence)} items."
            if include_variables:
                vars = [k for k in session.repl._namespace.keys() if not k.startswith("_")]
                if vars:
                    summary += f"\nVariables defined: {', '.join(vars)}."

            return f"## Summary So Far\n\n{summary}\n\n*Use this summary to keep your focus sharp.*"

    def _register_mcp_tools(self) -> None:
        _tool = self._tool_decorator

        @_tool()
        async def configure(
            sub_query_backend: Literal["api", "claude", "codex", "gemini", "auto"] | None = None,
            max_cmd_seconds: float | None = None,
            tool_docs_mode: Literal["concise", "full"] | None = None,
        ) -> str:
            """Update runtime configuration."""
            if sub_query_backend:
                self.sub_query_config.backend = sub_query_backend
            if max_cmd_seconds is not None:
                self.action_config.max_cmd_seconds = max_cmd_seconds
            if tool_docs_mode:
                self.tool_docs_mode = tool_docs_mode

            return "Configuration updated. Re-run `get_status` to see current values."

        @_tool()
        async def add_remote_server(
            server_id: str,
            command: str,
            args: list[str] | None = None,
            env: dict[str, str] | None = None,
            cwd: str | None = None,
            allow_tools: list[str] | None = None,
            deny_tools: list[str] | None = None,
            connect: bool = True,
            confirm: bool = False,
            output: Literal["markdown", "json", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Register a remote MCP server (stdio transport)."""
            err = self._require_actions(confirm)
            if err:
                return _format_error(err, output=output)

            handle = _RemoteServerHandle(
                command=command,
                args=args or [],
                env=env,
                cwd=Path(cwd) if cwd else None,
                allow_tools=allow_tools,
                deny_tools=deny_tools,
            )
            self._remote_servers[server_id] = handle

            if connect:
                success, error_msg = await self._ensure_remote_server(server_id)
                if not success:
                    return _format_error(str(error_msg), output=output)

            msg = f"Remote server '{server_id}' registered."
            if output == "object":
                return {"status": "success", "id": server_id}
            if output == "json":
                return json.dumps({"status": "success", "id": server_id})
            return msg

        @_tool()
        async def list_remote_servers(
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """List all registered remote MCP servers."""
            items = []
            for sid, handle in self._remote_servers.items():
                items.append({
                    "id": sid,
                    "connected": handle.session is not None,
                    "command": handle.command,
                    "connected_at": handle.connected_at.isoformat() if handle.connected_at else None,
                })

            if output == "object":
                return {"count": len(items), "items": items}
            if output == "json":
                return json.dumps({"count": len(items), "items": items}, indent=2)

            res = [f"Found {len(items)} remote server(s):\n"]
            for item in items:
                status = "connected" if item["connected"] else "not connected"
                res.append(f"- **{item['id']}** ({status}): `{item['command']}`")
            return "\n".join(res)

        @_tool()
        async def list_remote_tools(
            server_id: str,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """List tools available on a remote MCP server."""
            success, handle_or_err = await self._ensure_remote_server(server_id)
            if not success:
                return _format_error(str(handle_or_err), output=output)

            handle = cast(_RemoteServerHandle, handle_or_err)
            assert handle.session is not None
            tools = await handle.session.list_tools()

            items = []
            for t in tools.tools:
                if self._remote_tool_allowed(handle, t.name):
                    items.append({"name": t.name, "description": t.description})

            if output == "object":
                return {"server_id": server_id, "tools": items}
            if output == "json":
                return json.dumps({"server_id": server_id, "tools": items}, indent=2)

            res = [f"Tools on '{server_id}':\n"]
            for item in items:
                res.append(f"- **{item['name']}**: {item['description']}")
            return "\n".join(res)

        @_tool()
        async def call_remote_tool(
            server_id: str,
            tool: str,
            arguments: dict[str, Any] | None = None,
            recipe_id: str | None = None,
            timeout_seconds: float = 30,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "markdown",
        ) -> str | dict[str, Any]:
            """Call a tool on a remote MCP server."""
            success, handle_or_err = await self._ensure_remote_server(server_id)
            if not success:
                return _format_error(str(handle_or_err), output=output)

            handle = cast(_RemoteServerHandle, handle_or_err)
            if not self._remote_tool_allowed(handle, tool):
                return _format_error(f"Tool '{tool}' is denied on server '{server_id}'", output=output)

            assert handle.session is not None
            try:
                result = await handle.session.call_tool(tool, arguments or {})
            except Exception as e:
                return _format_error(f"Remote call failed: {e}", output=output)

            if output == "object":
                return {"result": result.content}
            if output == "json":
                return json.dumps({"result": [c.model_dump() for c in result.content]}, indent=2)

            res = []
            for c in result.content:
                if getattr(c, "text", None):
                    res.append(c.text)
                else:
                    res.append(str(c))
            return "\n".join(res)

        @_tool()
        async def close_remote_server(
            server_id: str,
            confirm: bool = False,
            output: Literal["json", "markdown", "object"] = "json",
        ) -> str | dict[str, Any]:
            """Close a remote MCP server connection."""
            if server_id not in self._remote_servers:
                return _format_error(f"Remote server '{server_id}' not registered.", output=output)

            handle = self._remote_servers[server_id]
            await self._reset_remote_server_handle(handle)

            msg = f"Remote server '{server_id}' disconnected."
            if output == "object":
                return {"status": "success", "id": server_id}
            if output == "json":
                return json.dumps({"status": "success", "id": server_id})
            return msg

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
    import argparse

    if len(sys.argv) > 1 and sys.argv[1] in {"run", "shell", "serve"}:
        from ..alef_cli import main as alef_main

        raise SystemExit(alef_main(sys.argv[1:]))

    def _parse_bool_flag(value: str) -> bool:
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise argparse.ArgumentTypeError("Expected a boolean value (true/false)")

    class _SafeArgumentParser(argparse.ArgumentParser):
        def _print_message(self, message: str, file: io.TextIOBase | None = None) -> None:
            if message:
                file = file or sys.stderr
                try:
                    file.write(message)
                except (AttributeError, OSError, ValueError):
                    pass

    parser = _SafeArgumentParser(
        description="Run Aleph as an MCP server for local AI reasoning",
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
        help="Workspace root for action tools (default: ALEPH_WORKSPACE_ROOT or auto-detect git root from invocation cwd)",
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
    parser.add_argument(
        "--sub-query-backend",
        type=str,
        choices=["codex", "claude", "gemini", "api", "auto"],
        default=None,
        help="Override sub-query backend (codex|claude|gemini|api|auto).",
    )
    parser.add_argument(
        "--sub-query-timeout",
        type=float,
        default=None,
        help="Timeout in seconds for sub-queries (sets ALEPH_SUB_QUERY_TIMEOUT).",
    )
    parser.add_argument(
        "--sub-query-share-session",
        type=_parse_bool_flag,
        default=None,
        help="Share MCP session with CLI sub-agents (true/false).",
    )

    # Swarm mode options
    parser.add_argument(
        "--swarm-mode",
        "-S",
        action="store_true",
        help="Enable swarm coordination features for multi-agent workflows.",
    )
    parser.add_argument(
        "--swarm-name",
        type=str,
        default=None,
        help="Swarm identifier for agent coordination (sets ALEPH_SWARM_NAME).",
    )
    parser.add_argument(
        "--enable-session-sharing",
        action="store_true",
        help="Enable sub-agent session sharing in swarm mode (sets ALEPH_SWARM_SESSION_SHARING=true).",
    )
    parser.add_argument(
        "--swarm-max-agents",
        type=int,
        default=None,
        help="Maximum concurrent agents in swarm (default: 10).",
    )
    parser.add_argument(
        "--swarm-context-prefix",
        type=str,
        default=None,
        help="Context ID prefix for swarm sessions (default: 'swarm').",
    )
    parser.add_argument(
        "--unrestricted",
        "-U",
        action="store_true",
        help="Disable sandbox restrictions (allow all imports, builtins, and AST constructs). Use with caution.",
    )

    args = parser.parse_args()

    if args.sub_query_backend is not None:
        os.environ["ALEPH_SUB_QUERY_BACKEND"] = args.sub_query_backend
    if args.sub_query_timeout is not None:
        os.environ["ALEPH_SUB_QUERY_TIMEOUT"] = str(args.sub_query_timeout)
    if args.sub_query_share_session is not None:
        os.environ["ALEPH_SUB_QUERY_SHARE_SESSION"] = (
            "true" if args.sub_query_share_session else "false"
        )

    # Swarm mode environment variables
    if args.swarm_mode:
        os.environ["ALEPH_SWARM_MODE"] = "true"
    if args.swarm_name is not None:
        os.environ["ALEPH_SWARM_NAME"] = args.swarm_name
    if args.enable_session_sharing:
        os.environ["ALEPH_SWARM_SESSION_SHARING"] = "true"
    if args.swarm_max_agents is not None:
        os.environ["ALEPH_SWARM_MAX_AGENTS"] = str(args.swarm_max_agents)
    if args.swarm_context_prefix is not None:
        os.environ["ALEPH_SWARM_CONTEXT_PREFIX"] = args.swarm_context_prefix

    config = SandboxConfig(
        timeout_seconds=args.timeout,
        max_output_chars=args.max_output,
        unrestricted=args.unrestricted,
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

"""Session models and serialization for MCP local server.

This is the canonical implementation for session state, evidence tracking,
and memory-pack serialization.  ``local_server.py`` imports from here —
do **not** duplicate these definitions elsewhere.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal

from ..repl.sandbox import REPLEnvironment, SandboxConfig
from ..types import ContentFormat, ContextMetadata
from .workspace import DEFAULT_LINE_NUMBER_BASE, LineNumberBase, _validate_line_number_base

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_PACK_RELATIVE_PATH = ".aleph/memory_pack.json"

EvidenceSource = Literal[
    "search", "peek", "exec", "manual", "action", "sub_query", "sub_aleph",
]
_VALID_EVIDENCE_SOURCES: set[str] = {
    "search", "peek", "exec", "manual", "action", "sub_query", "sub_aleph",
}

# ---------------------------------------------------------------------------
# Context locks — per-context asyncio locks for concurrent safety
# ---------------------------------------------------------------------------

_context_locks: dict[str, asyncio.Lock] = {}


def get_context_lock(context_id: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for a specific context_id."""
    if context_id not in _context_locks:
        _context_locks[context_id] = asyncio.Lock()
    return _context_locks[context_id]


def cleanup_context_lock(context_id: str) -> None:
    """Remove the lock for a context when it's deleted."""
    _context_locks.pop(context_id, None)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass
class _Evidence:
    """Provenance tracking for reasoning conclusions."""
    source: EvidenceSource
    line_range: tuple[int, int] | None
    pattern: str | None
    snippet: str
    note: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


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
    information_gain: list[int] = field(default_factory=list)
    # Chunk metadata for navigation
    chunks: list[dict] | None = None
    # Optional binding back to a workspace asset (file or generated manifest)
    workspace_binding: dict[str, Any] | None = None
    # Lightweight task tracking
    tasks: list[dict[str, Any]] = field(default_factory=list)
    task_counter: int = 0
    # Recursion depth tracking for sub_aleph
    max_depth_seen: int = 1
    # Evidence pruning: limit growth with FIFO eviction
    max_evidence: int = 100

    def add_evidence(self, ev: _Evidence, preserve_snippets: set[str] | None = None) -> None:
        """Add evidence with automatic FIFO pruning when limit exceeded."""
        self.evidence.append(ev)
        self._prune_evidence(preserve_snippets)

    def _prune_evidence(self, preserve_snippets: set[str] | None = None) -> None:
        """Prune oldest evidence when limit exceeded, preserving important entries."""
        if len(self.evidence) <= self.max_evidence:
            return

        preserve_snippets = preserve_snippets or set()
        protected: list[_Evidence] = []
        unprotected: list[_Evidence] = []

        for ev in self.evidence:
            if ev.snippet in preserve_snippets:
                protected.append(ev)
            else:
                unprotected.append(ev)

        slots_for_unprotected = max(0, self.max_evidence - len(protected))
        kept_unprotected = unprotected[-slots_for_unprotected:] if slots_for_unprotected > 0 else []

        self.evidence = sorted(
            protected + kept_unprotected,
            key=lambda e: e.timestamp,
        )

# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _session_to_payload(
    session_id: str,
    session: _Session,
    *,
    include_ctx: bool = True,
) -> dict[str, Any]:
    """Serialize a session to a JSON-safe dict.

    Args:
        session_id: Identifier for the session.
        session: The session to serialize.
        include_ctx: If False, redact the raw context and emit a ``ctx_redacted``
            flag with the character count instead.
    """
    ctx_val = session.repl.get_variable("ctx")
    ctx_text = _coerce_context_to_text(ctx_val)
    tasks_payload: list[dict[str, Any]] = []
    for task in session.tasks:
        if isinstance(task, dict):
            tasks_payload.append(task)

    payload: dict[str, Any] = {
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
        "think_history": list(session.think_history),
        "confidence_history": list(session.confidence_history),
        "information_gain": list(session.information_gain),
        "chunks": session.chunks,
        "workspace_binding": session.workspace_binding,
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
    if include_ctx:
        payload["ctx"] = ctx_text
    else:
        payload["ctx_redacted"] = True
        payload["ctx_chars"] = len(ctx_text)
    return payload


def snapshot_session_state(session: _Session) -> dict[str, Any]:
    """Capture the mutable reasoning/task state for a session."""
    reasoning_trace = session.repl._namespace.get("_reasoning_trace")
    if not isinstance(reasoning_trace, list):
        reasoning_trace = []
    return {
        "created_at": session.created_at,
        "iterations": session.iterations,
        "think_history": list(session.think_history),
        "evidence": list(session.evidence),
        "confidence_history": list(session.confidence_history),
        "information_gain": list(session.information_gain),
        "chunks": list(session.chunks) if isinstance(session.chunks, list) else session.chunks,
        "workspace_binding": (
            dict(session.workspace_binding)
            if isinstance(session.workspace_binding, dict)
            else None
        ),
        "tasks": [dict(task) for task in session.tasks if isinstance(task, dict)],
        "task_counter": session.task_counter,
        "max_depth_seen": session.max_depth_seen,
        "reasoning_trace": list(reasoning_trace),
    }


def restore_session_state(session: _Session, state: dict[str, Any]) -> None:
    """Restore mutable reasoning/task state onto an existing session."""
    session.created_at = state["created_at"]
    session.iterations = int(state["iterations"])
    session.think_history = list(state["think_history"])
    session.evidence = list(state["evidence"])
    session.confidence_history = list(state["confidence_history"])
    session.information_gain = list(state["information_gain"])
    chunks = state["chunks"]
    session.chunks = list(chunks) if isinstance(chunks, list) else chunks
    binding = state["workspace_binding"]
    session.workspace_binding = dict(binding) if isinstance(binding, dict) else None
    session.tasks = [dict(task) for task in state["tasks"] if isinstance(task, dict)]
    session.task_counter = int(state["task_counter"])
    session.max_depth_seen = int(state["max_depth_seen"])
    session.repl._namespace["_tasks"] = session.tasks
    reasoning_trace = state["reasoning_trace"]
    if reasoning_trace:
        session.repl._namespace["_reasoning_trace"] = list(reasoning_trace)
    else:
        session.repl._namespace.pop("_reasoning_trace", None)


def create_session(
    *,
    sessions: dict[str, _Session],
    context: str,
    context_id: str,
    fmt: ContentFormat,
    line_number_base: LineNumberBase,
    sandbox_config: SandboxConfig,
    analyze_text_context: Callable[[str, ContentFormat], ContextMetadata],
    configure_session: Callable[[_Session, str, asyncio.AbstractEventLoop | None], None],
    close_node_repl: Callable[[str], None] | None = None,
) -> ContextMetadata:
    """Create or replace a session for a context id."""
    if close_node_repl is not None:
        close_node_repl(context_id)

    meta = analyze_text_context(context, fmt)
    repl = REPLEnvironment(
        context=context,
        context_var_name="ctx",
        config=sandbox_config,
        loop=asyncio.get_running_loop(),
    )
    repl.set_variable("line_number_base", line_number_base)
    sessions[context_id] = _Session(
        repl=repl,
        meta=meta,
        line_number_base=line_number_base,
    )
    configure_session(sessions[context_id], context_id, asyncio.get_running_loop())
    return meta


def get_or_create_session(
    *,
    sessions: dict[str, _Session],
    context_id: str,
    line_number_base: LineNumberBase | None,
    sandbox_config: SandboxConfig,
    analyze_text_context: Callable[[str, ContentFormat], ContextMetadata],
    configure_session: Callable[[_Session, str, asyncio.AbstractEventLoop | None], None],
) -> _Session:
    """Get an existing session or create an empty one."""
    session = sessions.get(context_id)
    if session is not None:
        configure_session(session, context_id, asyncio.get_running_loop())
        return session

    base = (
        line_number_base
        if line_number_base is not None
        else DEFAULT_LINE_NUMBER_BASE
    )
    meta = analyze_text_context("", ContentFormat.TEXT)
    repl = REPLEnvironment(
        context="",
        context_var_name="ctx",
        config=sandbox_config,
        loop=asyncio.get_running_loop(),
    )
    repl.set_variable("line_number_base", base)
    session = _Session(repl=repl, meta=meta, line_number_base=base)
    sessions[context_id] = session
    configure_session(session, context_id, asyncio.get_running_loop())
    return session


def replace_session_context(
    *,
    sessions: dict[str, _Session],
    context: str,
    context_id: str,
    fmt: ContentFormat,
    line_number_base: LineNumberBase,
    sandbox_config: SandboxConfig,
    analyze_text_context: Callable[[str, ContentFormat], ContextMetadata],
    configure_session: Callable[[_Session, str, asyncio.AbstractEventLoop | None], None],
    close_node_repl: Callable[[str], None] | None = None,
    preserve_state: bool = False,
) -> ContextMetadata:
    """Replace the session context, optionally preserving reasoning/task state."""
    previous_state = None
    if preserve_state and context_id in sessions:
        previous_state = snapshot_session_state(sessions[context_id])

    meta = create_session(
        sessions=sessions,
        context=context,
        context_id=context_id,
        fmt=fmt,
        line_number_base=line_number_base,
        sandbox_config=sandbox_config,
        analyze_text_context=analyze_text_context,
        configure_session=configure_session,
        close_node_repl=close_node_repl,
    )
    if previous_state is not None:
        restore_session_state(sessions[context_id], previous_state)
    return meta


def build_memory_pack_payload(
    sessions: dict[str, _Session],
    *,
    include_ctx: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Serialize all known sessions into a memory-pack payload."""
    sessions_payload: list[dict[str, Any]] = []
    skipped: list[str] = []
    for sid, sess in sessions.items():
        try:
            sessions_payload.append(
                _session_to_payload(sid, sess, include_ctx=include_ctx)
            )
        except Exception:
            skipped.append(sid)
    payload = {
        "schema": "aleph.memory_pack.v1",
        "created_at": datetime.now().isoformat(),
        "sessions": sessions_payload,
        "skipped": skipped,
    }
    return payload, skipped


def _resolve_session_payload_id(session_payload: Any) -> str | None:
    if not isinstance(session_payload, dict):
        return None

    raw_id = session_payload.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()

    raw_session_id = session_payload.get("session_id")
    if isinstance(raw_session_id, str) and raw_session_id.strip():
        return raw_session_id.strip()

    raw_context_id = session_payload.get("context_id")
    if isinstance(raw_context_id, str) and raw_context_id.strip():
        return raw_context_id.strip()

    return None


def load_memory_pack_payload(
    payload: dict[str, Any],
    *,
    sessions: dict[str, _Session],
    sandbox_config: SandboxConfig,
    configure_session: Callable[[_Session, str, asyncio.AbstractEventLoop | None], None],
    loop: asyncio.AbstractEventLoop | None,
    close_node_repl: Callable[[str], None] | None = None,
    skip_existing: bool = False,
) -> tuple[list[str], list[dict[str, str]]]:
    """Load sessions from a memory-pack payload into the session registry."""
    if payload.get("schema") != "aleph.memory_pack.v1":
        raise ValueError("Invalid memory pack schema")

    session_payloads = payload.get("sessions")
    if not isinstance(session_payloads, list):
        raise ValueError("Invalid memory pack payload: sessions must be a list")

    loaded: list[str] = []
    skipped: list[dict[str, str]] = []
    for session_payload in session_payloads:
        resolved_id = _resolve_session_payload_id(session_payload)
        if not resolved_id:
            skipped.append({"id": "<missing>", "error": "missing session identifier"})
            continue
        if skip_existing and resolved_id in sessions:
            continue
        try:
            if close_node_repl is not None:
                close_node_repl(resolved_id)
            session = _session_from_payload(
                session_payload,
                resolved_id,
                sandbox_config,
                loop,
            )
            configure_session(session, resolved_id, loop)
            sessions[resolved_id] = session
            loaded.append(resolved_id)
        except Exception as exc:
            skipped.append({"id": resolved_id, "error": str(exc)})

    return loaded, skipped


def _session_from_payload(
    obj: dict[str, Any],
    resolved_id: str,
    sandbox_config: SandboxConfig,
    loop: asyncio.AbstractEventLoop | None,
) -> _Session:
    """Deserialize a session from a JSON payload."""
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
            tasks.append(dict(task))

    def _task_counter_seed(items: list[dict[str, Any]]) -> int:
        best = 0
        for task in items:
            raw_id = task.get("id")
            if isinstance(raw_id, int):
                best = max(best, raw_id)
                continue
            if isinstance(raw_id, str):
                digits = "".join(ch for ch in raw_id if ch.isdigit())
                if digits:
                    try:
                        best = max(best, int(digits))
                    except ValueError:
                        continue
        return best

    raw_task_counter = obj.get("task_counter")
    if isinstance(raw_task_counter, (int, str)):
        try:
            task_counter = int(raw_task_counter)
        except (TypeError, ValueError):
            task_counter = _task_counter_seed(tasks)
    else:
        task_counter = _task_counter_seed(tasks)

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
        workspace_binding=(
            dict(obj["workspace_binding"])
            if isinstance(obj.get("workspace_binding"), dict)
            else None
        ),
        tasks=tasks,
        task_counter=task_counter,
    )
    repl._namespace["_tasks"] = session.tasks

    ev_list = obj.get("evidence")
    if isinstance(ev_list, list):
        for ev in ev_list:
            if not isinstance(ev, dict):
                continue
            source = ev.get("source")
            if source not in _VALID_EVIDENCE_SOURCES:
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

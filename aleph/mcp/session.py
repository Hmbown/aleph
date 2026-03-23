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
from typing import Any, Literal

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
_VALID_EVIDENCE_SOURCES: set[str] = set(EvidenceSource.__args__)  # type: ignore[attr-defined]

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

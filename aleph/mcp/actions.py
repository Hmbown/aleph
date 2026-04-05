"""Action runtime helpers for the MCP local server.

This module keeps the action-family config and helper behavior out of
``local_server.py`` while leaving MCP tool registration in
``action_tools.py``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from .session import _Evidence, _Session
from .workspace import DEFAULT_WORKSPACE_MODE, WorkspaceMode, _detect_workspace_root

ContextPolicy = Literal["trusted", "isolated"]
DEFAULT_CONTEXT_POLICY: ContextPolicy = "trusted"
ActionPolicy = Literal["read-write", "read-only"]
DEFAULT_ACTION_POLICY: ActionPolicy = "read-write"

__all__ = [
    "ActionConfig",
    "require_actions",
    "record_action",
    "run_subprocess",
    "_parse_rg_vimgrep",
    "_python_rg_search",
]


@dataclass(slots=True)
class ActionConfig:
    enabled: bool = False
    workspace_root: Path = field(default_factory=_detect_workspace_root)
    workspace_mode: WorkspaceMode = DEFAULT_WORKSPACE_MODE
    context_policy: ContextPolicy = DEFAULT_CONTEXT_POLICY
    action_policy: ActionPolicy = DEFAULT_ACTION_POLICY
    require_confirmation: bool = False
    max_cmd_seconds: float = 60.0
    max_output_chars: int = 50_000
    max_read_bytes: int = 1_000_000_000  # Default 1GB. Increase if you have more RAM - the LLM only sees query results, not the file.
    max_write_bytes: int = 100_000_000  # 100 MB
    workspace_root_explicit: bool = (
        False  # True when set via CLI arg, env var, or configure()
    )


def require_actions(
    action_config: ActionConfig,
    confirm: bool,
    *,
    requires_write: bool = False,
    requires_command: bool = False,
) -> str | None:
    if not action_config.enabled:
        return "Actions are disabled. Start the server with `--enable-actions`."
    if action_config.require_confirmation and not confirm:
        return "Confirmation required. Re-run with confirm=true."
    if action_config.action_policy == "read-only" and (
        requires_write or requires_command
    ):
        if requires_command:
            return (
                "Action policy is read-only. Process execution is blocked. "
                "Re-run with `--action-policy read-write` or `configure(action_policy='read-write')`."
            )
        return (
            "Action policy is read-only. Filesystem writes are blocked. "
            "Re-run with `--action-policy read-write` or `configure(action_policy='read-write')`."
        )
    return None


def record_action(session: _Session | None, note: str, snippet: str) -> None:
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


async def run_subprocess(
    action_config: ActionConfig,
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
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        stdout_b, stderr_b = await proc.communicate()

    duration_ms = (time.perf_counter() - start) * 1000.0
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if len(stdout) > action_config.max_output_chars:
        stdout = stdout[: action_config.max_output_chars] + "\n... (truncated)"
    if len(stderr) > action_config.max_output_chars:
        stderr = stderr[: action_config.max_output_chars] + "\n... (truncated)"

    return {
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
    }


def _parse_rg_vimgrep(
    output: str,
    max_results: int,
) -> tuple[list[dict[str, Any]], bool]:
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
        results.append(
            {
                "path": path_str,
                "line": line_no,
                "column": col_no,
                "text": text,
            }
        )
        if limit is not None and len(results) >= limit:
            truncated = True
            break
    return results, truncated


def _python_rg_search(
    pattern: str,
    roots: list[Path],
    glob_pattern: str | None,
    max_results: int,
    max_read_bytes: int,
) -> tuple[list[dict[str, Any]], bool]:
    results: list[dict[str, Any]] = []
    truncated = False
    limit = max_results if max_results > 0 else None
    rx = re.compile(pattern)
    skip_dirs = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
    }

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
                if path.stat().st_size > max_read_bytes:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    for idx, line in enumerate(handle, start=1):
                        match = rx.search(line)
                        if not match:
                            continue
                        results.append(
                            {
                                "path": str(path),
                                "line": idx,
                                "column": match.start() + 1,
                                "text": line.rstrip("\n"),
                            }
                        )
                        if limit is not None and len(results) >= limit:
                            truncated = True
                            return results, truncated
            except Exception:
                continue
    return results, truncated

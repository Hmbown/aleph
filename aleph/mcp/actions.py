"""Action tool implementations for the MCP local server."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shlex
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from ..types import ContentFormat, ContextMetadata
from .formatting import _format_context_loaded, _format_error, _format_payload
from .session import _Evidence, _Session
from .workspace import (
    DEFAULT_LINE_NUMBER_BASE,
    DEFAULT_WORKSPACE_MODE,
    LineNumberBase,
    WorkspaceMode,
    _detect_workspace_root,
    _validate_line_number_base,
)


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


@dataclass(slots=True)
class ActionDeps:
    action_config: ActionConfig
    get_or_create_session: Callable[[str, LineNumberBase | None], _Session]
    create_session: Callable[[str, str, ContentFormat, LineNumberBase], ContextMetadata]
    scoped_path: Callable[[Path, str, WorkspaceMode], Path]
    load_text_from_path: Callable[[Path, int, float], tuple[str, ContentFormat, str | None]]


def require_actions(action_config: ActionConfig, confirm: bool) -> str | None:
    if not action_config.enabled:
        return "Actions are disabled. Start the server with `--enable-actions`."
    if action_config.require_confirmation and not confirm:
        return "Confirmation required. Re-run with confirm=true."
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
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
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
    max_read_bytes: int,
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
                if path.stat().st_size > max_read_bytes:
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

def _resolve_scoped_path(
    deps: ActionDeps,
    path: str,
) -> tuple[Path | None, str | None]:
    try:
        return (
            deps.scoped_path(
                deps.action_config.workspace_root,
                path,
                deps.action_config.workspace_mode,
            ),
            None,
        )
    except Exception as e:
        return None, str(e)


async def run_command(
    deps: ActionDeps,
    cmd: str,
    cwd: str | None = None,
    timeout_seconds: float | None = None,
    shell: bool = False,
    confirm: bool = False,
    output: Literal["json", "markdown", "object"] = "json",
    context_id: str = "default",
) -> str | dict[str, Any]:
    err = require_actions(deps.action_config, confirm)
    if err:
        return _format_error(err, output=output)

    session = deps.get_or_create_session(context_id, None)
    session.iterations += 1

    workspace_root = deps.action_config.workspace_root
    cwd_path = (
        deps.scoped_path(workspace_root, cwd, deps.action_config.workspace_mode)
        if cwd
        else workspace_root
    )
    timeout = timeout_seconds if timeout_seconds is not None else deps.action_config.max_cmd_seconds

    if shell:
        user_shell = os.environ.get("SHELL", "/bin/sh")
        argv = [user_shell, "-lc", cmd]
    else:
        argv = shlex.split(cmd)
        if not argv:
            return _format_error("Empty command", output=output)

    payload = await run_subprocess(action_config=deps.action_config, argv=argv, cwd=cwd_path, timeout_seconds=timeout)
    session.repl._namespace["last_command_result"] = payload
    record_action(session, note="run_command", snippet=(payload.get("stdout") or payload.get("stderr") or "")[:200])
    return _format_payload(payload, output=output)


async def rg_search(
    deps: ActionDeps,
    pattern: str,
    paths: list[str] | None = None,
    glob: str | None = None,
    max_results: int = 200,
    load_context_id: str | None = None,
    confirm: bool = False,
    output: Literal["json", "markdown", "object"] = "json",
    context_id: str = "default",
) -> str | dict[str, Any]:
    err = require_actions(deps.action_config, confirm)
    if err:
        return _format_error(err, output=output)
    if not pattern:
        return _format_error("pattern is required", output=output)

    session = deps.get_or_create_session(context_id, None)
    session.iterations += 1

    workspace_root = deps.action_config.workspace_root
    resolved_paths: list[Path] = []
    for p in paths or [str(workspace_root)]:
        resolved, err = _resolve_scoped_path(deps, p)
        if err:
            return _format_error(err, output=output)
        if resolved is not None:
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
        payload = await run_subprocess(
            action_config=deps.action_config,
            argv=argv,
            cwd=workspace_root,
            timeout_seconds=deps.action_config.max_cmd_seconds,
        )
        matches, truncated = _parse_rg_vimgrep(payload.get("stdout") or "", max_results)
    else:
        matches, truncated = _python_rg_search(
            pattern,
            resolved_paths,
            glob,
            max_results,
            deps.action_config.max_read_bytes,
        )

    hits_text = "\n".join(
        f"{m['path']}:{m['line']}:{m['column']}:{m['text']}" for m in matches
    )
    if load_context_id:
        meta = deps.create_session(hits_text, load_context_id, ContentFormat.TEXT, DEFAULT_LINE_NUMBER_BASE)
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
    record_action(session, note="rg_search", snippet=f"{pattern} ({len(matches)} matches)")

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


async def read_file(
    deps: ActionDeps,
    path: str,
    start_line: int = 1,
    limit: int = 200,
    include_raw: bool = False,
    line_number_base: int | None = None,
    confirm: bool = False,
    output: Literal["json", "markdown", "object"] = "json",
    context_id: str = "default",
) -> str | dict[str, Any]:
    err = require_actions(deps.action_config, confirm)
    if err:
        return _format_error(err, output=output)

    base_override: LineNumberBase | None = None
    if line_number_base is not None:
        try:
            base_override = _validate_line_number_base(line_number_base)
        except ValueError as e:
            return _format_error(str(e), output=output)

    session = deps.get_or_create_session(context_id, base_override)
    session.iterations += 1
    try:
        base = _resolve_line_number_base(session, line_number_base)
    except ValueError as e:
        return _format_error(str(e), output=output)

    if base == 1 and start_line == 0:
        start_line = 1
    if start_line < base:
        return _format_error(f"start_line must be >= {base}", output=output)

    p, err = _resolve_scoped_path(deps, path)
    if err or p is None:
        return _format_error(err or "Invalid path", output=output)

    if not p.exists() or not p.is_file():
        return _format_error(f"File not found: {path}", output=output)

    data = p.read_bytes()
    if len(data) > deps.action_config.max_read_bytes:
        return _format_error(
            f"File too large to read (>{deps.action_config.max_read_bytes} bytes): {path}",
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
    record_action(session, note="read_file", snippet=f"{path} ({start_line}-{end_line})")
    return _format_payload(payload, output=output)


async def load_file(
    deps: ActionDeps,
    path: str,
    context_id: str = "default",
    format: str = "auto",
    line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE,
    confirm: bool = False,
) -> str:
    err = require_actions(deps.action_config, confirm)
    if err:
        return f"Error: {err}"

    try:
        base = _validate_line_number_base(line_number_base)
    except ValueError as e:
        return f"Error: {e}"

    p, err = _resolve_scoped_path(deps, path)
    if err or p is None:
        return f"Error: {err or 'Invalid path'}"

    if not p.exists() or not p.is_file():
        return f"Error: File not found: {path}"

    try:
        text, detected_fmt, warning = deps.load_text_from_path(
            p,
            deps.action_config.max_read_bytes,
            deps.action_config.max_cmd_seconds,
        )
    except ValueError as e:
        return f"Error: {e}"
    try:
        fmt = detected_fmt if format == "auto" else ContentFormat(format)
    except Exception as e:
        return f"Error: {e}"
    meta = deps.create_session(text, context_id, fmt, base)
    session = deps.get_or_create_session(context_id, base)
    record_action(session, note="load_file", snippet=str(p))
    return _format_context_loaded(context_id, meta, base, note=warning)


async def write_file(
    deps: ActionDeps,
    path: str,
    content: str,
    mode: Literal["overwrite", "append"] = "overwrite",
    confirm: bool = False,
    output: Literal["json", "markdown", "object"] = "json",
    context_id: str = "default",
) -> str | dict[str, Any]:
    err = require_actions(deps.action_config, confirm)
    if err:
        return _format_error(err, output=output)

    session = deps.get_or_create_session(context_id, None)
    session.iterations += 1

    p, err = _resolve_scoped_path(deps, path)
    if err or p is None:
        return _format_error(err or "Invalid path", output=output)

    payload_bytes = content.encode("utf-8", errors="replace")
    if len(payload_bytes) > deps.action_config.max_write_bytes:
        return _format_error(
            f"Content too large to write (>{deps.action_config.max_write_bytes} bytes)",
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
    record_action(session, note="write_file", snippet=f"{path} ({len(payload_bytes)} bytes)")
    return _format_payload(payload, output=output)


async def run_tests(
    deps: ActionDeps,
    runner: Literal["auto", "pytest"] = "auto",
    args: list[str] | None = None,
    cwd: str | None = None,
    confirm: bool = False,
    output: Literal["json", "markdown", "object"] = "json",
    context_id: str = "default",
) -> str | dict[str, Any]:
    err = require_actions(deps.action_config, confirm)
    if err:
        return _format_error(err, output=output)

    session = deps.get_or_create_session(context_id, None)
    session.iterations += 1

    runner_resolved = "pytest" if runner == "auto" else runner
    if runner_resolved != "pytest":
        return _format_error(f"Unsupported test runner: {runner_resolved}", output=output)

    argv = [sys.executable, "-m", "pytest", "-vv", "--tb=short", "--maxfail=20"]
    if args:
        argv.extend(args)

    cwd_path = deps.action_config.workspace_root
    if cwd:
        resolved_cwd, err = _resolve_scoped_path(deps, cwd)
        if err or resolved_cwd is None:
            return _format_error(err or "Invalid path", output=output)
        cwd_path = resolved_cwd

    proc_payload = await run_subprocess(
        action_config=deps.action_config,
        argv=argv,
        cwd=cwd_path,
        timeout_seconds=deps.action_config.max_cmd_seconds,
    )
    stdout = str(proc_payload.get("stdout") or "")
    stderr = str(proc_payload.get("stderr") or "")
    raw_output = stdout + ("\n" + stderr if stderr else "")

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

    session.repl._namespace["last_test_result"] = result
    summary_snippet = (
        f"status={status} passed={passed} failed={failed} errors={errors} "
        f"failures={len(failures)} exit_code={exit_code}"
    )
    record_action(session, note="run_tests", snippet=summary_snippet)
    for f in failures[:10]:
        record_action(session, note="test_failure", snippet=(f.get("message") or f.get("test_name") or "")[:200])
    return _format_payload(result, output=output)

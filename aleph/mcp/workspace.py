"""Workspace and path helpers for MCP local server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import unquote, urlparse


LineNumberBase = Literal[0, 1]
DEFAULT_LINE_NUMBER_BASE: LineNumberBase = 1
WorkspaceMode = Literal["fixed", "git", "any"]
DEFAULT_WORKSPACE_MODE: WorkspaceMode = "fixed"


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


def _validate_line_number_base(value: int) -> LineNumberBase:
    if value not in (0, 1):
        raise ValueError("line_number_base must be 0 or 1")
    return cast(LineNumberBase, value)


def _resolve_line_number_base(session: Any | None, value: int | None) -> LineNumberBase:
    if session is not None:
        if value is None:
            return cast(LineNumberBase, session.line_number_base)
        base = _validate_line_number_base(value)
        if base != session.line_number_base:
            raise ValueError("line_number_base does not match existing session")
        return base
    if value is None:
        return DEFAULT_LINE_NUMBER_BASE
    return _validate_line_number_base(value)


def _uri_to_path(uri: str) -> Path | None:
    """Convert a file:// URI to a Path, or return None for non-file URIs."""
    try:
        parsed = urlparse(uri)
    except Exception:
        return None
    if parsed.scheme != "file":
        return None
    decoded = unquote(parsed.path)
    if not decoded:
        return None
    p = Path(decoded)
    try:
        p = p.resolve()
    except Exception:
        pass
    return p


def roots_to_workspace_root(roots: list[Any]) -> Path | None:
    """Pick the best workspace root from MCP Root objects.

    Each root has a ``uri`` (``file://`` URI) and optional ``name``.
    Prefers a root that contains a ``.git`` directory; otherwise uses the first
    valid directory.
    """
    paths: list[Path] = []
    for root in roots:
        uri = getattr(root, "uri", None)
        if not isinstance(uri, str):
            continue
        p = _uri_to_path(uri)
        if p is None:
            continue
        if p.is_dir():
            paths.append(p)

    if not paths:
        return None

    # Prefer a root with a .git directory
    for p in paths:
        if (p / ".git").exists():
            return p

    return paths[0]

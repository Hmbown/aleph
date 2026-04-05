"""Workspace-backed context helpers for MCP sessions.

These helpers let Aleph treat some contexts as refreshable workspace assets
instead of anonymous blobs. The first supported bindings are:

- file: a context loaded from a workspace file via ``load_file``
- manifest: a generated workspace manifest for large codebases/projects
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Iterable

from ..types import ContentFormat
from .io_utils import _load_text_from_path

WorkspaceBinding = dict[str, Any]

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".next",
    ".parcel-cache",
    "coverage",
}

_IMPORTANT_FILES = {
    "README.md",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    ".env.example",
}

_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".jsonl": "jsonl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".toml": "toml",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
}


def _display_path(path: Path, workspace_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace_root.resolve()))
    except Exception:
        return str(path.resolve())


def make_file_binding(path: Path, workspace_root: Path) -> WorkspaceBinding:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "kind": "file",
        "path": str(resolved),
        "workspace_root": str(workspace_root.resolve()),
        "display_path": _display_path(resolved, workspace_root),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "refreshed_at": datetime.now().isoformat(),
    }


def _iter_workspace_files(root: Path, include_hidden: bool) -> Iterable[Path]:
    if root.is_file():
        yield root
        return

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _SKIP_DIRS and (include_hidden or not d.startswith("."))
        )
        for filename in sorted(filenames):
            if not include_hidden and filename.startswith("."):
                continue
            path = current / filename
            try:
                if path.is_file():
                    yield path
            except (PermissionError, OSError):
                continue


def _language_for_path(path: Path) -> str:
    if path.name in _IMPORTANT_FILES:
        return "project-config"
    suffix = path.suffix.lower()
    if suffix in _LANGUAGE_BY_SUFFIX:
        return _LANGUAGE_BY_SUFFIX[suffix]
    if not suffix:
        return "plain"
    return suffix.lstrip(".")


def build_workspace_manifest(
    *,
    workspace_root: Path,
    roots: list[Path],
    max_files: int = 2000,
    include_hidden: bool = False,
) -> tuple[str, WorkspaceBinding, str | None]:
    resolved_workspace_root = workspace_root.resolve()
    resolved_roots = [root.resolve() for root in roots]

    files: list[tuple[str, str, int]] = []
    language_counts: Counter[str] = Counter()
    top_level_counts: Counter[str] = Counter()
    important_files: list[str] = []
    truncated = False

    for root in resolved_roots:
        for path in _iter_workspace_files(root, include_hidden=include_hidden):
            language = _language_for_path(path)
            display = _display_path(path, resolved_workspace_root)
            try:
                stat = path.stat()
                size_bytes = stat.st_size
            except OSError:
                size_bytes = 0

            files.append((display, language, size_bytes))
            language_counts[language] += 1
            top_component = display.split("/", 1)[0] if "/" in display else display
            top_level_counts[top_component] += 1
            if path.name in _IMPORTANT_FILES and display not in important_files:
                important_files.append(display)

            if len(files) >= max_files:
                truncated = True
                break
        if truncated:
            break

    lines: list[str] = [
        "# Aleph Workspace Manifest",
        "",
        f"Workspace root: {resolved_workspace_root}",
        f"Generated at: {datetime.now().isoformat()}",
        "",
        "Indexed roots:",
    ]
    for root in resolved_roots:
        lines.append(f"- {_display_path(root, resolved_workspace_root)}")

    lines.extend(
        [
            "",
            f"Files indexed: {len(files)}" + (f" (truncated at {max_files})" if truncated else ""),
            "",
            "Language summary:",
        ]
    )
    for language, count in language_counts.most_common(12):
        lines.append(f"- {language}: {count}")

    if top_level_counts:
        lines.extend(["", "Top-level paths:"])
        for name, count in top_level_counts.most_common(12):
            lines.append(f"- {name}: {count}")

    if important_files:
        lines.extend(["", "Key project files:"])
        for path in sorted(important_files)[:20]:
            lines.append(f"- {path}")

    lines.extend(["", "File listing:"])
    for display, language, size_bytes in files:
        lines.append(f"- {display} | {language} | {size_bytes} bytes")

    note: str | None = None
    if truncated:
        note = f"Manifest truncated at {max_files} files. Increase max_files for broader coverage."

    binding: WorkspaceBinding = {
        "kind": "manifest",
        "workspace_root": str(resolved_workspace_root),
        "roots": [str(root) for root in resolved_roots],
        "max_files": max_files,
        "include_hidden": include_hidden,
        "file_count": len(files),
        "truncated": truncated,
        "refreshed_at": datetime.now().isoformat(),
    }
    return "\n".join(lines), binding, note


def binding_summary(binding: WorkspaceBinding | None) -> str | None:
    if not binding:
        return None
    kind = str(binding.get("kind") or "")
    if kind == "file":
        return f"file:{binding.get('display_path') or binding.get('path')}"
    if kind == "manifest":
        file_count = binding.get("file_count")
        return f"manifest:{file_count} files"
    return kind or None


def binding_status(binding: WorkspaceBinding | None) -> dict[str, Any] | None:
    if not binding:
        return None

    kind = str(binding.get("kind") or "")
    if kind == "file":
        path_text = binding.get("path")
        if not isinstance(path_text, str):
            return {
                "kind": "file",
                "exists": False,
                "refreshable": True,
                "stale": True,
                "reason": "missing file path",
            }
        path = Path(path_text)
        if not path.exists():
            return {
                "kind": "file",
                "path": path_text,
                "display_path": binding.get("display_path"),
                "exists": False,
                "refreshable": True,
                "stale": True,
                "reason": "file missing",
            }
        try:
            expected_size = int(binding.get("size_bytes") or 0)
            expected_mtime_ns = int(binding.get("mtime_ns") or 0)
        except (TypeError, ValueError):
            return {
                "kind": "file",
                "path": path_text,
                "display_path": binding.get("display_path"),
                "exists": True,
                "refreshable": True,
                "stale": True,
                "reason": "invalid persisted file metadata",
                "last_refreshed_at": binding.get("refreshed_at"),
            }
        try:
            stat = path.stat()
        except OSError:
            return {
                "kind": "file",
                "path": path_text,
                "display_path": binding.get("display_path"),
                "exists": True,
                "refreshable": True,
                "stale": True,
                "reason": "unable to stat file",
                "last_refreshed_at": binding.get("refreshed_at"),
            }
        stale = (
            stat.st_size != expected_size
            or stat.st_mtime_ns != expected_mtime_ns
        )
        reason = "file changed on disk" if stale else None
        return {
            "kind": "file",
            "path": path_text,
            "display_path": binding.get("display_path"),
            "exists": True,
            "refreshable": True,
            "stale": stale,
            "reason": reason,
            "last_refreshed_at": binding.get("refreshed_at"),
        }

    if kind == "manifest":
        return {
            "kind": "manifest",
            "exists": True,
            "roots": list(binding.get("roots") or []),
            "file_count": int(binding.get("file_count") or 0),
            "truncated": bool(binding.get("truncated") or False),
            "refreshable": True,
            "stale": False,
            "reason": None,
            "last_refreshed_at": binding.get("refreshed_at"),
        }

    return {
        "kind": kind or "unknown",
        "exists": False,
        "refreshable": False,
        "stale": False,
        "reason": None,
    }


def refresh_workspace_binding(
    binding: WorkspaceBinding,
    *,
    max_read_bytes: int,
    timeout_seconds: float,
) -> tuple[str, ContentFormat, str | None, WorkspaceBinding]:
    kind = str(binding.get("kind") or "")
    if kind == "file":
        path_text = binding.get("path")
        workspace_root_text = binding.get("workspace_root")
        if not isinstance(path_text, str) or not isinstance(workspace_root_text, str):
            raise ValueError("Invalid file binding: missing path metadata")
        path = Path(path_text)
        workspace_root = Path(workspace_root_text)
        text, fmt, warning = _load_text_from_path(path, max_read_bytes, timeout_seconds)
        return text, fmt, warning, make_file_binding(path, workspace_root)

    if kind == "manifest":
        workspace_root_text = binding.get("workspace_root")
        roots_text = binding.get("roots")
        if not isinstance(workspace_root_text, str) or not isinstance(roots_text, list):
            raise ValueError("Invalid manifest binding: missing roots metadata")
        roots = [Path(str(root)) for root in roots_text]
        text, new_binding, note = build_workspace_manifest(
            workspace_root=Path(workspace_root_text),
            roots=roots,
            max_files=int(binding.get("max_files") or 2000),
            include_hidden=bool(binding.get("include_hidden") or False),
        )
        return text, ContentFormat.TEXT, note, new_binding

    raise ValueError("Context is not bound to a refreshable workspace asset")

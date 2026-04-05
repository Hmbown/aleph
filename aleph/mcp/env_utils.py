"""Environment helper utilities for MCP local server."""

from __future__ import annotations

import os

from ..settings import MCPServerEnvSettings


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


DEFAULT_REMOTE_TOOL_TIMEOUT_SECONDS = MCPServerEnvSettings().remote_tool_timeout_seconds

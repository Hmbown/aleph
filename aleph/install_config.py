"""Pure installer profile and MCP config assembly helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .sub_query import (
    DEFAULT_CLAUDE_EFFORT,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_MODE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
)

INSTALL_PROFILES = ("portable", "claude", "codex", "api")
DEFAULT_SUB_QUERY_PROFILE_TIMEOUT = 300.0


@dataclass
class MCPServerConfig:
    command: str
    args: list[str]
    env: dict[str, str]
    # Cursor / VS Code MCP configs accept an explicit transport key ("type": "stdio").
    transport: str | None = None

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "command": self.command,
            "args": self.args,
        }
        if self.env:
            payload["env"] = self.env
        if self.transport:
            payload["type"] = self.transport
        return payload


def normalize_install_profile(profile: str) -> str:
    normalized = profile.strip().lower()
    if normalized not in INSTALL_PROFILES:
        allowed = ", ".join(INSTALL_PROFILES)
        raise ValueError(f"Unsupported install profile '{profile}'. Choose from: {allowed}.")
    return normalized


def profile_args(profile: str) -> list[str]:
    normalized = normalize_install_profile(profile)
    if normalized == "portable":
        return []
    if normalized == "claude":
        return [
            "--sub-query-backend",
            "claude",
            "--sub-query-timeout",
            str(DEFAULT_SUB_QUERY_PROFILE_TIMEOUT),
            "--sub-query-share-session",
            "true",
            "--sub-query-claude-model",
            DEFAULT_CLAUDE_MODEL,
            "--sub-query-claude-effort",
            DEFAULT_CLAUDE_EFFORT,
        ]
    if normalized == "codex":
        return [
            "--sub-query-backend",
            "codex",
            "--sub-query-timeout",
            str(DEFAULT_SUB_QUERY_PROFILE_TIMEOUT),
            "--sub-query-share-session",
            "true",
            "--sub-query-codex-mode",
            DEFAULT_CODEX_MODE,
            "--sub-query-codex-model",
            DEFAULT_CODEX_MODEL,
            "--sub-query-codex-reasoning-effort",
            DEFAULT_CODEX_REASONING_EFFORT,
        ]
    return [
        "--sub-query-backend",
        "api",
        "--sub-query-timeout",
        str(DEFAULT_SUB_QUERY_PROFILE_TIMEOUT),
    ]


def install_profile_options() -> list[tuple[str, str]]:
    return [
        (
            "portable",
            "portable (no sub-query backend pinned; plugin-safe default)",
        ),
        (
            "claude",
            f"claude (Claude backend, model={DEFAULT_CLAUDE_MODEL}, effort={DEFAULT_CLAUDE_EFFORT})",
        ),
        (
            "codex",
            f"codex (Codex backend, model={DEFAULT_CODEX_MODEL}, effort={DEFAULT_CODEX_REASONING_EFFORT})",
        ),
        (
            "api",
            "api (force API backend; model/key handled separately)",
        ),
    ]


def default_install_profile_choice(
    profile_options: list[str],
    *,
    claude_available: bool,
    codex_available: bool,
) -> int:
    if "claude" in profile_options and claude_available:
        return profile_options.index("claude")
    if "codex" in profile_options and codex_available:
        return profile_options.index("codex")
    if "portable" in profile_options:
        return profile_options.index("portable")
    return 0


def default_mcp_config(profile: str = "portable") -> MCPServerConfig:
    return MCPServerConfig(
        command="aleph",
        args=[
            "--enable-actions",
            "--workspace-mode",
            "any",
            "--tool-docs",
            "concise",
            *profile_args(profile),
        ],
        env={},
    )


def default_mcp_config_cursor_project(profile: str = "portable") -> MCPServerConfig:
    """Defaults for Cursor project-scoped `.cursor/mcp.json`.

    Uses ``${workspaceFolder}`` so action tools resolve to the opened folder and
    ``--workspace-mode fixed`` matches that root (see Cursor MCP variable docs).
    """
    return MCPServerConfig(
        command="aleph",
        args=[
            "--enable-actions",
            "--workspace-root",
            "${workspaceFolder}",
            "--workspace-mode",
            "fixed",
            "--tool-docs",
            "concise",
            *profile_args(profile),
        ],
        env={},
        transport="stdio",
    )


def mcp_server_config_for_client(client_key: str, profile: str = "portable") -> MCPServerConfig:
    """Pick installer defaults that fit each MCP client's config scope."""
    if client_key == "cursor-project":
        return default_mcp_config_cursor_project(profile)
    if client_key == "cursor":
        base = default_mcp_config(profile)
        return MCPServerConfig(
            command=base.command,
            args=list(base.args),
            env=dict(base.env),
            transport="stdio",
        )
    return default_mcp_config(profile)


def build_mcp_config(
    *,
    enable_actions: bool,
    workspace_mode: str,
    workspace_root: Path | None,
    require_confirmation: bool,
    tool_docs: str,
    unrestricted: bool,
    sub_query_profile: str,
    env_override: dict[str, str] | None = None,
    command: str | None = None,
    args_prefix: list[str] | None = None,
) -> MCPServerConfig:
    args: list[str] = list(args_prefix or [])
    if enable_actions:
        args.append("--enable-actions")
    if workspace_root:
        args.extend(["--workspace-root", str(workspace_root)])
    args.extend(["--workspace-mode", workspace_mode])
    if require_confirmation:
        args.append("--require-confirmation")
    args.extend(["--tool-docs", tool_docs])
    if unrestricted:
        args.append("--unrestricted")
    args.extend(profile_args(sub_query_profile))

    return MCPServerConfig(
        command=command or "aleph",
        args=args,
        env=dict(env_override or {}),
    )


def _format_toml_array(values: list[str]) -> str:
    quoted = [json.dumps(v) for v in values]
    return "[" + ", ".join(quoted) + "]"


def _format_toml_env(env: dict[str, str]) -> str:
    if not env:
        return ""
    lines = ["[mcp_servers.aleph.env]"]
    for key in sorted(env.keys()):
        lines.append(f"{key} = {json.dumps(env[key])}")
    return "\n".join(lines) + "\n"


def format_toml_mcp_config(config: MCPServerConfig) -> str:
    block = (
        "[mcp_servers.aleph]\n"
        f"command = {json.dumps(config.command)}\n"
        f"args = {_format_toml_array(config.args)}\n"
    )
    return block + _format_toml_env(config.env)

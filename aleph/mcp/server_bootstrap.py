"""CLI parser and runtime bootstrap helpers for the local MCP server."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable

from ..settings import MCPServerEnvSettings
from ..sub_query import (
    DEFAULT_CLAUDE_EFFORT,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_MODE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
)
from .sub_query_runtime import apply_sub_query_cli_env_overrides


def parse_bool_flag(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("Expected a boolean value (true/false)")


def resolve_default_tool_docs(default_tool_docs_mode: str) -> str:
    if os.environ.get("ALEPH_TOOL_DOCS") is not None:
        return MCPServerEnvSettings().tool_docs
    return default_tool_docs_mode


class SafeArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message: str, file: Any = None) -> None:
        if message:
            target = file or sys.stderr
            try:
                target.write(message)
            except (AttributeError, OSError, ValueError):
                pass


def build_server_argument_parser(
    *,
    default_workspace_mode: str,
    default_tool_docs_mode: str,
) -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Run Aleph as an MCP server for local AI reasoning",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Code execution timeout in seconds (default: 180)",
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
        help="Enable action tools (run_command/read_file/write_file/load_file/run_tests/rg_search)",
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
        default=default_workspace_mode,
        help="Path scope for action tools: fixed (workspace root only), git (any git repo), any (no path restriction)",
    )
    parser.add_argument(
        "--action-policy",
        type=str,
        choices=["read-write", "read-only"],
        default=None,
        help="Filesystem/process policy for action tools: read-write (default) or read-only.",
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
    parser.add_argument(
        "--tool-docs",
        type=str,
        choices=["concise", "full"],
        default=resolve_default_tool_docs(default_tool_docs_mode),
        help="Tool description verbosity for MCP clients: concise (default) or full",
    )
    parser.add_argument(
        "--sub-query-backend",
        type=str,
        choices=["codex", "claude", "gemini", "kimi", "api", "auto"],
        default=None,
        help=(
            "Override sub-query backend "
            "(codex|claude|gemini|kimi|api|auto). "
            "Auto mode only selects codex or api; other CLI backends are explicit experimental overrides."
        ),
    )
    parser.add_argument(
        "--sub-query-timeout",
        type=float,
        default=None,
        help="Timeout in seconds for sub-queries (sets ALEPH_SUB_QUERY_TIMEOUT).",
    )
    parser.add_argument(
        "--sub-query-share-session",
        type=parse_bool_flag,
        default=None,
        help="Share MCP session with CLI sub-agents (true/false).",
    )
    parser.add_argument(
        "--sub-query-api-model",
        type=str,
        default=None,
        help="Pin the API backend model (sets ALEPH_SUB_QUERY_MODEL).",
    )
    parser.add_argument(
        "--sub-query-claude-model",
        type=str,
        default=None,
        help=(
            "Pin the Claude CLI model alias/name for sub-queries "
            f"(default alias: {DEFAULT_CLAUDE_MODEL})."
        ),
    )
    parser.add_argument(
        "--sub-query-claude-effort",
        type=str,
        default=None,
        help=(
            "Pin the Claude CLI effort for sub-queries "
            f"(default: {DEFAULT_CLAUDE_EFFORT})."
        ),
    )
    parser.add_argument(
        "--sub-query-codex-mode",
        type=str,
        choices=["exec", "mcp"],
        default=None,
        help=f"Pin the Codex sub-query mode (default: {DEFAULT_CODEX_MODE}).",
    )
    parser.add_argument(
        "--sub-query-codex-model",
        type=str,
        default=None,
        help=f"Pin the Codex sub-query model (default: {DEFAULT_CODEX_MODEL}).",
    )
    parser.add_argument(
        "--sub-query-codex-reasoning-effort",
        type=str,
        default=None,
        help=(
            "Pin the Codex reasoning effort for sub-queries "
            f"(default: {DEFAULT_CODEX_REASONING_EFFORT})."
        ),
    )
    parser.add_argument(
        "--sub-query-codex-profile",
        type=str,
        default=None,
        help="Pin the Codex profile used for sub-queries.",
    )
    parser.add_argument(
        "--context-policy",
        type=str,
        choices=["trusted", "isolated"],
        default=None,
        help="Context policy mode: trusted (default) or isolated.",
    )
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
    return parser


def apply_server_env_overrides(args: argparse.Namespace) -> None:
    apply_sub_query_cli_env_overrides(
        sub_query_backend=args.sub_query_backend,
        sub_query_timeout=args.sub_query_timeout,
        sub_query_share_session=args.sub_query_share_session,
        sub_query_api_model=args.sub_query_api_model,
        sub_query_claude_model=args.sub_query_claude_model,
        sub_query_claude_effort=args.sub_query_claude_effort,
        sub_query_codex_mode=args.sub_query_codex_mode,
        sub_query_codex_model=args.sub_query_codex_model,
        sub_query_codex_reasoning_effort=args.sub_query_codex_reasoning_effort,
        sub_query_codex_profile=args.sub_query_codex_profile,
        context_policy=args.context_policy,
    )

    if args.swarm_mode:
        os.environ["ALEPH_SWARM_MODE"] = "true"
    if getattr(args, "action_policy", None) is not None:
        os.environ["ALEPH_ACTION_POLICY"] = args.action_policy
    if args.swarm_name is not None:
        os.environ["ALEPH_SWARM_NAME"] = args.swarm_name
    if args.enable_session_sharing:
        os.environ["ALEPH_SWARM_SESSION_SHARING"] = "true"
    if args.swarm_max_agents is not None:
        os.environ["ALEPH_SWARM_MAX_AGENTS"] = str(args.swarm_max_agents)
    if args.swarm_context_prefix is not None:
        os.environ["ALEPH_SWARM_CONTEXT_PREFIX"] = args.swarm_context_prefix


def build_runtime_configs(
    args: argparse.Namespace,
    *,
    detect_workspace_root: Callable[[], Path],
    normalize_context_policy: Callable[[str | None, str], str],
    normalize_action_policy: Callable[[str | None, str], str],
    default_context_policy: str,
    default_action_policy: str,
    sandbox_config_factory: Callable[..., Any],
    action_config_factory: Callable[..., Any],
) -> tuple[Any, Any, str]:
    env_settings = MCPServerEnvSettings()
    sandbox_config = sandbox_config_factory(
        timeout_seconds=args.timeout,
        max_output_chars=args.max_output,
        unrestricted=args.unrestricted,
    )

    workspace_root_explicit = bool(args.workspace_root) or bool(env_settings.workspace_root)
    action_config = action_config_factory(
        enabled=bool(args.enable_actions),
        workspace_root=(
            Path(args.workspace_root).resolve()
            if args.workspace_root
            else detect_workspace_root()
        ),
        workspace_mode=args.workspace_mode,
        context_policy=normalize_context_policy(
            env_settings.context_policy,
            default_context_policy,
        ),
        action_policy=normalize_action_policy(
            getattr(args, "action_policy", None) or env_settings.action_policy,
            default_action_policy,
        ),
        require_confirmation=bool(args.require_confirmation),
        max_read_bytes=args.max_file_size,
        max_write_bytes=args.max_write_bytes,
        workspace_root_explicit=workspace_root_explicit,
    )
    return sandbox_config, action_config, args.tool_docs

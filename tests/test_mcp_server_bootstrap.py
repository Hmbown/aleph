from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aleph.mcp.local_server import (
    DEFAULT_ACTION_POLICY,
    DEFAULT_CONTEXT_POLICY,
    DEFAULT_TOOL_DOCS_MODE,
    DEFAULT_WORKSPACE_MODE,
    ActionConfig,
    SandboxConfig,
    _normalize_action_policy,
    _normalize_context_policy,
)
from aleph.mcp.server_bootstrap import (
    apply_server_env_overrides,
    build_runtime_configs,
    build_server_argument_parser,
)


def test_build_server_argument_parser_parses_false_boolean_flag():
    parser = build_server_argument_parser(
        default_workspace_mode=DEFAULT_WORKSPACE_MODE,
        default_tool_docs_mode=DEFAULT_TOOL_DOCS_MODE,
    )

    args = parser.parse_args(["--sub-query-share-session", "false"])

    assert args.sub_query_share_session is False


def test_apply_server_env_overrides_sets_sub_query_and_swarm_env():
    args = SimpleNamespace(
        sub_query_backend="codex",
        sub_query_timeout=42.0,
        sub_query_share_session=False,
        sub_query_api_model="gpt-4.1-mini",
        sub_query_claude_model="claude-test",
        sub_query_claude_effort="medium",
        sub_query_codex_mode="exec",
        sub_query_codex_model="gpt-5.4-mini",
        sub_query_codex_reasoning_effort="high",
        sub_query_codex_profile="subquery",
        context_policy="isolated",
        action_policy="read-only",
        swarm_mode=True,
        swarm_name="release-cutover",
        enable_session_sharing=True,
        swarm_max_agents=7,
        swarm_context_prefix="swarm",
    )

    with patch.dict(os.environ, {}, clear=True):
        apply_server_env_overrides(args)

        assert os.environ["ALEPH_SUB_QUERY_BACKEND"] == "codex"
        assert os.environ["ALEPH_SUB_QUERY_TIMEOUT"] == "42.0"
        assert os.environ["ALEPH_SUB_QUERY_SHARE_SESSION"] == "false"
        assert os.environ["ALEPH_SUB_QUERY_MODEL"] == "gpt-4.1-mini"
        assert os.environ["ALEPH_SUB_QUERY_CLAUDE_MODEL"] == "claude-test"
        assert os.environ["ALEPH_SUB_QUERY_CLAUDE_EFFORT"] == "medium"
        assert os.environ["ALEPH_SUB_QUERY_CODEX_MODE"] == "exec"
        assert os.environ["ALEPH_SUB_QUERY_CODEX_MODEL"] == "gpt-5.4-mini"
        assert os.environ["ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT"] == "high"
        assert os.environ["ALEPH_SUB_QUERY_CODEX_PROFILE"] == "subquery"
        assert os.environ["ALEPH_CONTEXT_POLICY"] == "isolated"
        assert os.environ["ALEPH_ACTION_POLICY"] == "read-only"
        assert os.environ["ALEPH_SWARM_MODE"] == "true"
        assert os.environ["ALEPH_SWARM_NAME"] == "release-cutover"
        assert os.environ["ALEPH_SWARM_SESSION_SHARING"] == "true"
        assert os.environ["ALEPH_SWARM_MAX_AGENTS"] == "7"
        assert os.environ["ALEPH_SWARM_CONTEXT_PREFIX"] == "swarm"


def test_build_runtime_configs_uses_explicit_workspace_root(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    auto_root = tmp_path / "auto-root"
    auto_root.mkdir()

    args = SimpleNamespace(
        timeout=12.5,
        max_output=1234,
        unrestricted=True,
        enable_actions=True,
        workspace_root=str(workspace_root),
        workspace_mode="git",
        require_confirmation=True,
        max_file_size=456,
        max_write_bytes=789,
        tool_docs="full",
        action_policy=None,
    )

    with patch.dict(os.environ, {}, clear=True):
        sandbox_config, action_config, tool_docs_mode = build_runtime_configs(
            args,
            detect_workspace_root=lambda: auto_root,
            normalize_context_policy=_normalize_context_policy,
            normalize_action_policy=_normalize_action_policy,
            default_context_policy=DEFAULT_CONTEXT_POLICY,
            default_action_policy=DEFAULT_ACTION_POLICY,
            sandbox_config_factory=SandboxConfig,
            action_config_factory=ActionConfig,
        )

    assert sandbox_config.timeout_seconds == 12.5
    assert sandbox_config.max_output_chars == 1234
    assert sandbox_config.unrestricted is True
    assert action_config.enabled is True
    assert action_config.workspace_root == workspace_root.resolve()
    assert action_config.workspace_mode == "git"
    assert action_config.context_policy == DEFAULT_CONTEXT_POLICY
    assert action_config.action_policy == DEFAULT_ACTION_POLICY
    assert action_config.require_confirmation is True
    assert action_config.max_read_bytes == 456
    assert action_config.max_write_bytes == 789
    assert action_config.workspace_root_explicit is True
    assert tool_docs_mode == "full"

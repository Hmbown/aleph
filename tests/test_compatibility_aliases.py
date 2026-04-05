from __future__ import annotations

import os

import pytest

from aleph.core import Aleph
from aleph.mcp.actions import ActionConfig as RuntimeActionConfig, require_actions
from aleph.mcp.local_server import ActionConfig, AlephMCPServerLocal
from aleph.types import ContentFormat


def test_aleph_accepts_minimal_output_feedback_alias() -> None:
    aleph = Aleph(provider="anthropic", output_feedback="minimal")
    assert aleph.output_feedback == "metadata"


def test_server_normalizes_output_feedback_env_alias(sandbox_config) -> None:
    os.environ["ALEPH_OUTPUT_FEEDBACK"] = "minimal"
    server = AlephMCPServerLocal(sandbox_config=sandbox_config)
    assert server.output_feedback == "metadata"


def test_local_server_reexports_runtime_action_config() -> None:
    assert ActionConfig is RuntimeActionConfig


def test_runtime_require_actions_preserves_read_only_guards() -> None:
    cfg = RuntimeActionConfig(enabled=True, action_policy="read-only")

    command_error = require_actions(cfg, confirm=True, requires_command=True)
    write_error = require_actions(cfg, confirm=True, requires_write=True)

    assert command_error is not None
    assert "read-only" in command_error
    assert "Process execution is blocked" in command_error

    assert write_error is not None
    assert "read-only" in write_error
    assert "Filesystem writes are blocked" in write_error


def test_runtime_require_actions_preserves_confirmation_guard() -> None:
    cfg = RuntimeActionConfig(enabled=True, require_confirmation=True)

    error = require_actions(cfg, confirm=False)

    assert error == "Confirmation required. Re-run with confirm=true."


@pytest.mark.asyncio
async def test_configure_accepts_minimal_output_feedback_alias(sandbox_config) -> None:
    server = AlephMCPServerLocal(sandbox_config=sandbox_config)

    result = await server.server._tool_manager.call_tool(
        "configure",
        {"output_feedback": "minimal"},
        convert_result=False,
    )

    assert isinstance(result, str)
    assert "Configuration updated" in result
    assert server.output_feedback == "metadata"
    assert os.environ["ALEPH_OUTPUT_FEEDBACK"] == "metadata"


@pytest.mark.asyncio
async def test_load_context_accepts_markdown_format_alias(sandbox_config) -> None:
    server = AlephMCPServerLocal(sandbox_config=sandbox_config)

    result = await server.server._tool_manager.call_tool(
        "load_context",
        {
            "content": "# Heading\nBody",
            "context_id": "doc",
            "format": "markdown",
        },
        convert_result=False,
    )

    assert isinstance(result, str)
    assert "Context loaded 'doc'" in result
    assert server._sessions["doc"].meta.format == ContentFormat.TEXT


@pytest.mark.asyncio
async def test_load_file_accepts_markdown_format_alias(sandbox_config, tmp_path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Heading\nBody\n", encoding="utf-8")

    server = AlephMCPServerLocal(
        sandbox_config=sandbox_config,
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path, workspace_mode="any"),
    )

    result = await server.server._tool_manager.call_tool(
        "load_file",
        {
            "path": str(path),
            "context_id": "doc",
            "format": "markdown",
        },
        convert_result=False,
    )

    assert isinstance(result, str)
    assert "Context loaded 'doc'" in result
    assert server._sessions["doc"].meta.format == ContentFormat.TEXT

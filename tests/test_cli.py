"""Tests for CLI installer, especially Windows compatibility."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from aleph.cli import (
    _apply_client_mcp_defaults,
    _collect_install_config,
    _default_install_profile_choice,
    _default_mcp_config,
    _find_claude_cli,
    _json_config_issues,
    is_client_installed,
    CLIENTS,
    MCPServerConfig,
)
from aleph.install_config import mcp_server_config_for_client
from aleph.sub_query import (
    DEFAULT_CLAUDE_EFFORT,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_MODE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
)


class TestFindClaudeCli:
    """Tests for _find_claude_cli() Windows compatibility (issue #17)."""

    def test_find_claude_standard_unix(self) -> None:
        """Test finding 'claude' on Unix-like systems."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/claude"
            result = _find_claude_cli()
            assert result == "claude"
            mock_which.assert_called_once_with("claude")

    def test_find_claude_not_found(self) -> None:
        """Test when claude is not found anywhere."""
        with patch("shutil.which", return_value=None):
            with patch("platform.system", return_value="Linux"):
                result = _find_claude_cli()
                assert result is None

    def test_find_claude_windows_cmd(self) -> None:
        """Test finding claude.cmd on Windows (NPM installation)."""
        def mock_which(name: str) -> str | None:
            if name == "claude.cmd":
                return "C:\\Users\\test\\AppData\\Roaming\\npm\\claude.cmd"
            return None

        with patch("shutil.which", side_effect=mock_which):
            with patch("platform.system", return_value="Windows"):
                result = _find_claude_cli()
                assert result == "claude.cmd"

    def test_find_claude_windows_ps1(self) -> None:
        """Test finding claude.ps1 on Windows."""
        def mock_which(name: str) -> str | None:
            if name == "claude.ps1":
                return "C:\\Users\\test\\AppData\\Roaming\\npm\\claude.ps1"
            return None

        with patch("shutil.which", side_effect=mock_which):
            with patch("platform.system", return_value="Windows"):
                result = _find_claude_cli()
                assert result == "claude.ps1"

    def test_find_claude_windows_exe(self) -> None:
        """Test finding claude.exe on Windows."""
        def mock_which(name: str) -> str | None:
            if name == "claude.exe":
                return "C:\\Program Files\\Claude\\claude.exe"
            return None

        with patch("shutil.which", side_effect=mock_which):
            with patch("platform.system", return_value="Windows"):
                result = _find_claude_cli()
                assert result == "claude.exe"

    def test_find_claude_windows_npm_appdata_fallback(self) -> None:
        """Test fallback to npm APPDATA path when shutil.which fails."""
        with patch("shutil.which", return_value=None):
            with patch("platform.system", return_value="Windows"):
                with patch.dict(os.environ, {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}):
                    with patch.object(Path, "exists", return_value=True):
                        result = _find_claude_cli()
                        # Should return the full path from npm
                        assert result is not None
                        assert "npm" in result
                        assert "claude.cmd" in result or "claude.ps1" in result

    def test_find_claude_prefers_standard_name(self) -> None:
        """Test that 'claude' is preferred over Windows extensions."""
        def mock_which(name: str) -> str | None:
            # Both exist, but 'claude' should be preferred
            if name == "claude":
                return "/usr/local/bin/claude"
            if name == "claude.cmd":
                return "C:\\somewhere\\claude.cmd"
            return None

        with patch("shutil.which", side_effect=mock_which):
            result = _find_claude_cli()
            assert result == "claude"


class TestIsClientInstalled:
    """Tests for is_client_installed() with Claude Code client."""

    def test_claude_code_installed(self) -> None:
        """Test detection when Claude Code CLI is available."""
        with patch("aleph.cli._find_claude_cli", return_value="claude"):
            client = CLIENTS["claude-code"]
            assert is_client_installed(client) is True

    def test_claude_code_not_installed(self) -> None:
        """Test detection when Claude Code CLI is not available."""
        with patch("aleph.cli._find_claude_cli", return_value=None):
            client = CLIENTS["claude-code"]
            assert is_client_installed(client) is False

    def test_claude_code_windows_cmd_installed(self) -> None:
        """Test detection when Claude Code is installed as .cmd on Windows."""
        with patch("aleph.cli._find_claude_cli", return_value="claude.cmd"):
            client = CLIENTS["claude-code"]
            assert is_client_installed(client) is True


class TestDefaultInstallProfileChoice:
    def test_prefers_claude_when_available(self) -> None:
        with patch("aleph.cli._find_claude_cli", return_value="claude"):
            assert _default_install_profile_choice(["portable", "claude", "codex", "api"]) == 1

    def test_falls_back_to_codex_when_claude_missing(self) -> None:
        with patch("aleph.cli._find_claude_cli", return_value=None):
            with patch("shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None):
                assert _default_install_profile_choice(["portable", "claude", "codex", "api"]) == 2

    def test_falls_back_to_portable_when_no_cli_available(self) -> None:
        with patch("aleph.cli._find_claude_cli", return_value=None):
            with patch("shutil.which", return_value=None):
                assert _default_install_profile_choice(["portable", "claude", "codex", "api"]) == 0


class TestCollectInstallConfig:
    def test_builds_claude_profile_args(self) -> None:
        def fake_prompt_bool(prompt: str, default: bool = False) -> bool:
            if prompt.startswith("Run Aleph inside Docker"):
                return False
            if prompt.startswith("Enable action tools"):
                return True
            if prompt.startswith("Require confirm=true"):
                return False
            if prompt.startswith("Disable sandbox restrictions"):
                return False
            raise AssertionError(f"Unexpected bool prompt: {prompt}")

        def fake_prompt_choice(prompt: str, options, default_index: int = 0):  # type: ignore[no-untyped-def]
            if prompt.startswith("Workspace scope for action tools"):
                return "git"
            if prompt.startswith("Tool docs verbosity"):
                return "concise"
            raise AssertionError(f"Unexpected choice prompt: {prompt}")

        with patch("shutil.which", return_value=None):
            with patch("aleph.cli._prompt_bool", side_effect=fake_prompt_bool):
                with patch("aleph.cli._prompt_choice", side_effect=fake_prompt_choice):
                    config = _collect_install_config("claude")

        assert config.command == "aleph"
        assert "--sub-query-backend" in config.args
        assert config.args[config.args.index("--sub-query-backend") + 1] == "claude"
        assert "--sub-query-claude-model" in config.args
        assert config.args[config.args.index("--sub-query-claude-model") + 1] == DEFAULT_CLAUDE_MODEL
        assert "--sub-query-claude-effort" in config.args
        assert config.args[config.args.index("--sub-query-claude-effort") + 1] == DEFAULT_CLAUDE_EFFORT
        assert "--sub-query-share-session" in config.args
        assert config.args[config.args.index("--sub-query-share-session") + 1] == "true"


class TestLocalServerCli:
    def test_help_lists_kimi_backend(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "aleph.mcp.local_server", "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert "kimi" in result.stdout


class TestInstallProfiles:
    def test_portable_profile_leaves_sub_query_unpinned(self) -> None:
        config = _default_mcp_config("portable")
        assert "--sub-query-backend" not in config.args
        assert config.env == {}

    def test_claude_profile_pins_claude_defaults(self) -> None:
        config = _default_mcp_config("claude")
        assert config.args[config.args.index("--sub-query-backend") + 1] == "claude"
        assert config.args[config.args.index("--sub-query-claude-model") + 1] == DEFAULT_CLAUDE_MODEL
        assert config.args[config.args.index("--sub-query-claude-effort") + 1] == DEFAULT_CLAUDE_EFFORT
        assert config.args[config.args.index("--sub-query-share-session") + 1] == "true"

    def test_codex_profile_pins_codex_defaults(self) -> None:
        config = _default_mcp_config("codex")
        assert config.args[config.args.index("--sub-query-backend") + 1] == "codex"
        assert config.args[config.args.index("--sub-query-codex-mode") + 1] == DEFAULT_CODEX_MODE
        assert config.args[config.args.index("--sub-query-codex-model") + 1] == DEFAULT_CODEX_MODEL
        assert (
            config.args[config.args.index("--sub-query-codex-reasoning-effort") + 1]
            == DEFAULT_CODEX_REASONING_EFFORT
        )

    def test_client_defaults_no_longer_mutate_profile(self) -> None:
        original = MCPServerConfig(
            command="aleph",
            args=["--enable-actions", "--sub-query-backend", "claude"],
            env={"KEEP": "1"},
            transport="stdio",
        )
        config = _apply_client_mcp_defaults(CLIENTS["codex"], original)
        assert config.command == original.command
        assert config.args == original.args
        assert config.env == original.env
        assert config.transport == original.transport


class TestMcpServerConfigForClient:
    def test_cursor_project_uses_workspace_folder_and_fixed(self) -> None:
        cfg = mcp_server_config_for_client("cursor-project", "portable")
        assert cfg.transport == "stdio"
        assert cfg.args[cfg.args.index("--workspace-root") + 1] == "${workspaceFolder}"
        assert cfg.args[cfg.args.index("--workspace-mode") + 1] == "fixed"

    def test_cursor_global_keeps_any_workspace_and_sets_stdio(self) -> None:
        cfg = mcp_server_config_for_client("cursor", "portable")
        assert cfg.transport == "stdio"
        assert cfg.args[cfg.args.index("--workspace-mode") + 1] == "any"

    def test_non_cursor_clients_omit_transport(self) -> None:
        assert mcp_server_config_for_client("claude-desktop", "portable").transport is None

    def test_cursor_project_json_includes_type_stdio(self) -> None:
        payload = mcp_server_config_for_client("cursor-project", "portable").to_json()
        assert payload.get("type") == "stdio"


class TestJsonConfigIssues:
    def test_cursor_requires_stdio_type(self) -> None:
        issues = _json_config_issues(
            CLIENTS["cursor"],
            {
                "command": "aleph",
                "args": ["--enable-actions", "--workspace-mode", "any"],
            },
        )
        assert issues == ['expected `type: "stdio"` for Cursor MCP.']

    def test_cursor_project_requires_fixed_workspace_mode(self) -> None:
        issues = _json_config_issues(
            CLIENTS["cursor-project"],
            {
                "type": "stdio",
                "command": "aleph",
                "args": ["--enable-actions", "--workspace-mode", "any"],
            },
        )
        assert issues == ["expected `--workspace-mode fixed` for project-scoped Cursor MCP."]

    def test_cursor_project_accepts_stdio_and_fixed_mode(self) -> None:
        issues = _json_config_issues(
            CLIENTS["cursor-project"],
            {
                "type": "stdio",
                "command": "aleph",
                "args": [
                    "--enable-actions",
                    "--workspace-root",
                    "${workspaceFolder}",
                    "--workspace-mode",
                    "fixed",
                ],
            },
        )
        assert issues == []

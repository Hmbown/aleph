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
    _default_sub_query_backend_choice,
    _find_claude_cli,
    is_client_installed,
    CLIENTS,
    MCPServerConfig,
)
from aleph.sub_query import DEFAULT_CODEX_MODE, DEFAULT_CODEX_MODEL, DEFAULT_CODEX_REASONING_EFFORT


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


class TestDefaultSubQueryBackendChoice:
    def test_prefers_codex_when_available(self) -> None:
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None):
            assert _default_sub_query_backend_choice(["auto", "codex", "gemini", "claude", "api"]) == 1

    def test_falls_back_to_auto_when_codex_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            assert _default_sub_query_backend_choice(["auto", "codex", "gemini", "claude", "api"]) == 0


class TestCollectInstallConfig:
    def test_offers_kimi_backend_when_available(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_prompt_bool(prompt: str, default: bool = False) -> bool:
            if prompt.startswith("Enable action tools"):
                return True
            if prompt.startswith("Require confirm=true"):
                return False
            if prompt.startswith("Disable sandbox restrictions"):
                return False
            if prompt.startswith("Share MCP session"):
                return False
            raise AssertionError(f"Unexpected bool prompt: {prompt}")

        def fake_prompt_choice(prompt: str, options, default_index: int = 0):  # type: ignore[no-untyped-def]
            if prompt.startswith("Workspace scope for action tools"):
                return "git"
            if prompt.startswith("Tool docs verbosity"):
                return "concise"
            if prompt.startswith("Sub-query backend preference"):
                captured["backend_options"] = [value for value, _label in options]
                return "auto"
            raise AssertionError(f"Unexpected choice prompt: {prompt}")

        with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name == "kimi" else None):
            with patch("aleph.cli._prompt_bool", side_effect=fake_prompt_bool):
                with patch("aleph.cli._prompt_choice", side_effect=fake_prompt_choice):
                    with patch("aleph.cli._prompt_text", return_value=""):
                        _collect_install_config()

        assert captured["backend_options"] == ["auto", "codex", "gemini", "kimi", "claude", "api"]


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


class TestCodexClientDefaults:
    def test_codex_client_defaults_pin_codex_mcp_env(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/codex"):
            config = _apply_client_mcp_defaults(
                CLIENTS["codex"],
                MCPServerConfig(
                    command="aleph",
                    args=["--enable-actions"],
                    env={},
                ),
            )

        assert config.env["ALEPH_SUB_QUERY_BACKEND"] == "codex"
        assert config.env["ALEPH_SUB_QUERY_CODEX_MODE"] == DEFAULT_CODEX_MODE
        assert config.env["ALEPH_SUB_QUERY_CODEX_MODEL"] == DEFAULT_CODEX_MODEL
        assert (
            config.env["ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT"]
            == DEFAULT_CODEX_REASONING_EFFORT
        )
        assert config.env["ALEPH_SUB_QUERY_SHARE_SESSION"] == "true"

    def test_codex_client_defaults_preserve_explicit_env(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/codex"):
            config = _apply_client_mcp_defaults(
                CLIENTS["codex"],
                MCPServerConfig(
                    command="aleph",
                    args=["--enable-actions"],
                    env={
                        "ALEPH_SUB_QUERY_BACKEND": "api",
                        "ALEPH_SUB_QUERY_SHARE_SESSION": "false",
                        "ALEPH_SUB_QUERY_CODEX_MODEL": "custom-model",
                    },
                ),
            )

        assert config.env["ALEPH_SUB_QUERY_BACKEND"] == "api"
        assert config.env["ALEPH_SUB_QUERY_SHARE_SESSION"] == "false"
        assert config.env["ALEPH_SUB_QUERY_CODEX_MODEL"] == "custom-model"
        assert config.env["ALEPH_SUB_QUERY_CODEX_MODE"] == DEFAULT_CODEX_MODE

    def test_other_clients_pin_codex_when_codex_cli_is_available(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/codex"):
            config = _apply_client_mcp_defaults(
                CLIENTS["claude-code"],
                MCPServerConfig(
                    command="aleph",
                    args=["--enable-actions"],
                    env={},
                ),
            )

        assert config.env["ALEPH_SUB_QUERY_BACKEND"] == "codex"
        assert config.env["ALEPH_SUB_QUERY_SHARE_SESSION"] == "true"

    def test_other_clients_do_not_pin_codex_when_cli_is_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            config = _apply_client_mcp_defaults(
                CLIENTS["claude-code"],
                MCPServerConfig(
                    command="aleph",
                    args=["--enable-actions"],
                    env={},
                ),
            )

        assert "ALEPH_SUB_QUERY_BACKEND" not in config.env

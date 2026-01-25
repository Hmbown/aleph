"""Tests for CLI installer, especially Windows compatibility."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from aleph.cli import _find_claude_cli, is_client_installed, CLIENTS


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

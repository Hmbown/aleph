"""Tests for sub_query module (RLM-style recursive reasoning)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from aleph.sub_query import SubQueryConfig, detect_backend, has_api_credentials
from aleph.sub_query.cli_backend import run_cli_sub_query, CLI_BACKENDS
from aleph.sub_query.api_backend import run_api_sub_query


class TestSubQueryConfig:
    """Tests for SubQueryConfig."""

    def test_default_config(self):
        config = SubQueryConfig()
        assert config.backend == "auto"
        assert config.api_model == "mimo-v2-flash"
        assert config.api_key_env == "MIMO_API_KEY"
        assert config.api_base_url_env == "OPENAI_BASE_URL"
        assert config.max_context_chars == 100_000

    def test_custom_config(self):
        config = SubQueryConfig(
            backend="api",
            api_model="gpt-4o-mini",
            max_context_chars=50_000,
        )
        assert config.backend == "api"
        assert config.api_model == "gpt-4o-mini"
        assert config.max_context_chars == 50_000


class TestDetectBackend:
    """Tests for backend detection."""

    def test_detect_backend_claude(self):
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda x: "/usr/bin/claude" if x == "claude" else None
            assert detect_backend() == "claude"

    def test_detect_backend_codex(self):
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
            assert detect_backend() == "codex"

    def test_detect_backend_aider(self):
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda x: "/usr/bin/aider" if x == "aider" else None
            assert detect_backend() == "aider"

    def test_detect_backend_api_fallback(self):
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            assert detect_backend() == "api"


class TestHasApiCredentials:
    """Tests for API credential detection."""

    def test_has_credentials(self):
        with patch.dict(os.environ, {"MIMO_API_KEY": "test-key"}):
            assert has_api_credentials() is True

    def test_no_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove both API keys if they exist
            os.environ.pop("MIMO_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            assert has_api_credentials() is False


class TestCliBackend:
    """Tests for CLI backend."""

    @pytest.mark.asyncio
    async def test_cli_not_found(self):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = FileNotFoundError("claude not found")
            success, output = await run_cli_sub_query(
                prompt="test",
                backend="claude",
            )
            assert success is False
            assert "not found" in output.lower()

    @pytest.mark.asyncio
    async def test_cli_success(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Test response", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="claude",
                timeout=10.0,
            )
            assert success is True
            assert output == "Test response"

    @pytest.mark.asyncio
    async def test_cli_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, output = await run_cli_sub_query(
                prompt="test",
                backend="claude",
                timeout=0.1,
            )
            assert success is False
            assert "timeout" in output.lower()

    @pytest.mark.asyncio
    async def test_cli_with_context(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Result with context", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            success, output = await run_cli_sub_query(
                prompt="Summarize this:",
                context_slice="Some important text here.",
                backend="claude",
            )
            assert success is True
            # Verify the command was called (exact args depend on backend)
            mock_exec.assert_called_once()


class TestApiBackend:
    """Tests for API backend."""

    @pytest.mark.asyncio
    async def test_api_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            success, output = await run_api_sub_query(
                prompt="test",
            )
            assert success is False
            assert "API key not found" in output

    @pytest.mark.asyncio
    async def test_api_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "API response"}}]
        }

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://api.test.com/v1"}):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                success, output = await run_api_sub_query(
                    prompt="test prompt",
                )
                assert success is True
                assert output == "API response"

    @pytest.mark.asyncio
    async def test_api_error_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.json.return_value = {"error": {"message": "Server error"}}

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                success, output = await run_api_sub_query(prompt="test")
                assert success is False
                assert "500" in output

    @pytest.mark.asyncio
    async def test_api_with_system_prompt(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}}]
        }

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                success, output = await run_api_sub_query(
                    prompt="test",
                    system_prompt="You are a helpful assistant.",
                )
                assert success is True

                # Verify system prompt was included
                call_args = mock_instance.post.call_args
                payload = call_args.kwargs.get("json", call_args.args[1] if len(call_args.args) > 1 else {})
                messages = payload.get("messages", [])
                assert any(m.get("role") == "system" for m in messages)


class TestCliBackends:
    """Tests for CLI_BACKENDS constant."""

    def test_cli_backends_tuple(self):
        assert isinstance(CLI_BACKENDS, tuple)
        assert "claude" in CLI_BACKENDS
        assert "codex" in CLI_BACKENDS
        assert "aider" in CLI_BACKENDS

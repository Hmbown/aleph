"""Tests for sub_query module (RLM-style recursive reasoning)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from aleph.sub_query import (
    SubQueryConfig,
    detect_backend,
    has_api_credentials,
    DEFAULT_API_KEY_ENV,
    DEFAULT_API_BASE_URL_ENV,
    DEFAULT_API_MODEL_ENV,
)
from aleph.sub_query.cli_backend import run_cli_sub_query, CLI_BACKENDS
from aleph.sub_query.api_backend import run_api_sub_query


class TestSubQueryConfig:
    """Tests for SubQueryConfig."""

    def test_default_config(self):
        config = SubQueryConfig()
        assert config.backend == "auto"
        assert config.max_context_chars == 100_000
        assert config.api_key_env == DEFAULT_API_KEY_ENV
        assert config.api_base_url_env == DEFAULT_API_BASE_URL_ENV
        assert config.api_model_env == DEFAULT_API_MODEL_ENV
        assert config.validation_regex is None
        assert config.max_retries == 0

    def test_custom_config(self):
        config = SubQueryConfig(
            backend="api",
            max_context_chars=50_000,
            api_model="gpt-4o",
            validation_regex=r"^OK:",
            max_retries=2,
        )
        assert config.backend == "api"
        assert config.max_context_chars == 50_000
        assert config.api_model == "gpt-4o"
        assert config.validation_regex == r"^OK:"
        assert config.max_retries == 2


class TestDetectBackend:
    """Tests for backend detection.

    Priority order (CLI-first):
    1. ALEPH_SUB_QUERY_BACKEND env var (explicit override)
    2. codex CLI (if installed)
    3. gemini CLI (if installed)
    4. claude CLI (if installed)
    5. API fallback (will error with helpful message)
    """

    def test_detect_backend_cli_preferred_with_aleph_key(self):
        """CLI should be preferred even when ALEPH_SUB_QUERY_API_KEY is set."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_API_KEY": "test-key"}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
                assert detect_backend() == "codex"

    def test_detect_backend_cli_preferred_with_openai_key(self):
        """CLI should be preferred even when OPENAI_API_KEY is set (fallback)."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
                assert detect_backend() == "codex"

    def test_detect_backend_explicit_override(self):
        """ALEPH_SUB_QUERY_BACKEND should override all other detection."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "codex", "ALEPH_SUB_QUERY_API_KEY": "key"}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.return_value = "/usr/bin/something"
                assert detect_backend() == "codex"

    def test_detect_backend_explicit_override_api(self):
        """ALEPH_SUB_QUERY_BACKEND=api should force API even without credentials."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "api"}, clear=True):
            assert detect_backend() == "api"

    def test_detect_backend_claude_when_no_codex_gemini(self):
        """Claude CLI should be used when codex/gemini are unavailable."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/claude" if x == "claude" else None
                assert detect_backend() == "claude"

    def test_detect_backend_codex_when_available(self):
        """Codex CLI should be used when available."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
                assert detect_backend() == "codex"

    def test_detect_backend_gemini_when_no_codex(self):
        """Gemini CLI should be used when codex is unavailable."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/gemini" if x == "gemini" else None
                assert detect_backend() == "gemini"

    def test_detect_backend_api_fallback(self):
        """API fallback when nothing else available (will error with helpful message)."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.return_value = None
                assert detect_backend() == "api"

    def test_detect_backend_model_override_does_not_beat_cli(self):
        """ALEPH_SUB_QUERY_MODEL should not override CLI preference."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_MODEL": "gpt-5.2-codex", "OPENAI_API_KEY": "key"}, clear=True):
            with patch("aleph.sub_query.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
                assert detect_backend() == "codex"


class TestHasApiCredentials:
    """Tests for API credential detection."""

    def test_has_aleph_credentials(self):
        """ALEPH_SUB_QUERY_API_KEY should be detected."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_API_KEY": "test-key"}, clear=True):
            assert has_api_credentials() is True

    def test_has_openai_credentials_fallback(self):
        """OPENAI_API_KEY should be detected as fallback."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            assert has_api_credentials() is True

    def test_no_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
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
        """Should error without API key."""
        with patch.dict(os.environ, {}, clear=True):
            success, output = await run_api_sub_query(prompt="test")
            assert success is False
            assert "No API key found" in output

    @pytest.mark.asyncio
    async def test_api_no_model(self):
        """Should error without model configured."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_API_KEY": "test-key"}, clear=True):
            success, output = await run_api_sub_query(prompt="test")
            assert success is False
            assert "No model configured" in output

    @pytest.mark.asyncio
    async def test_api_success(self):
        """Should succeed with key and model."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "API response"}}]
        }

        with patch.dict(
            os.environ,
            {"ALEPH_SUB_QUERY_API_KEY": "test-key", "ALEPH_SUB_QUERY_MODEL": "gpt-5.2-codex"},
            clear=True,
        ):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                success, output = await run_api_sub_query(prompt="test prompt")
                assert success is True
                assert output == "API response"

    @pytest.mark.asyncio
    async def test_api_openai_fallback(self):
        """Should work with OPENAI_API_KEY fallback."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OpenAI response"}}]
        }

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test", "ALEPH_SUB_QUERY_MODEL": "gpt-5.2-codex"},
            clear=True,
        ):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                success, output = await run_api_sub_query(prompt="test prompt")
                assert success is True
                assert output == "OpenAI response"

    @pytest.mark.asyncio
    async def test_api_error_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.json.return_value = {"error": {"message": "Server error"}}

        with patch.dict(
            os.environ,
            {"ALEPH_SUB_QUERY_API_KEY": "test-key", "ALEPH_SUB_QUERY_MODEL": "gpt-5.2-codex"},
            clear=True,
        ):
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

        with patch.dict(
            os.environ,
            {"ALEPH_SUB_QUERY_API_KEY": "test-key", "ALEPH_SUB_QUERY_MODEL": "gpt-5.2-codex"},
            clear=True,
        ):
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

    @pytest.mark.asyncio
    async def test_api_model_override_param(self):
        """Explicit model parameter should override env var."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}}]
        }

        with patch.dict(
            os.environ,
            {"ALEPH_SUB_QUERY_API_KEY": "test-key", "ALEPH_SUB_QUERY_MODEL": "env-model"},
            clear=True,
        ):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                success, output = await run_api_sub_query(
                    prompt="test",
                    model="explicit-model",  # Should override env
                )
                assert success is True

                # Verify explicit model was used
                call_args = mock_instance.post.call_args
                payload = call_args.kwargs.get("json", call_args.args[1] if len(call_args.args) > 1 else {})
                assert payload.get("model") == "explicit-model"

    @pytest.mark.asyncio
    async def test_api_custom_base_url(self):
        """Custom base URL via env var should be used."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}}]
        }

        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_API_KEY": "test-key",
                "ALEPH_SUB_QUERY_MODEL": "llama-3.1",
                "ALEPH_SUB_QUERY_URL": "https://api.groq.com/openai/v1",
            },
            clear=True,
        ):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                success, output = await run_api_sub_query(prompt="test")
                assert success is True

                # Verify correct URL was called
                call_args = mock_instance.post.call_args
                url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
                assert "groq.com" in url


class TestCliBackends:
    """Tests for CLI_BACKENDS constant."""

    def test_cli_backends_tuple(self):
        assert isinstance(CLI_BACKENDS, tuple)
        assert "claude" in CLI_BACKENDS
        assert "codex" in CLI_BACKENDS
        assert "gemini" in CLI_BACKENDS

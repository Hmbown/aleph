"""Tests for sub_query module (RLM-style recursive reasoning)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from aleph.sub_query import (
    SubQueryConfig,
    DEFAULT_CLAUDE_EFFORT,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_MODE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    detect_backend,
    has_api_credentials,
    DEFAULT_API_KEY_ENV,
    DEFAULT_API_BASE_URL_ENV,
    DEFAULT_API_MODEL_ENV,
)
from aleph.sub_query.cli_backend import run_cli_sub_query, CLI_BACKENDS
from aleph.sub_query.codex_mcp_backend import (
    build_codex_mcp_tool_call,
    compose_sub_query_prompt,
    extract_codex_mcp_result_text,
)
from aleph.sub_query.api_backend import run_api_sub_query


class TestSubQueryConfig:
    """Tests for SubQueryConfig."""

    def test_default_config(self):
        config = SubQueryConfig()
        assert config.backend == "auto"
        assert config.max_context_chars == 20_000
        assert config.api_key_env == DEFAULT_API_KEY_ENV
        assert config.api_base_url_env == DEFAULT_API_BASE_URL_ENV
        assert config.api_model_env == DEFAULT_API_MODEL_ENV
        assert config.validation_regex is None
        assert config.max_retries == 0
        assert config.claude_model == DEFAULT_CLAUDE_MODEL
        assert config.claude_effort == DEFAULT_CLAUDE_EFFORT
        assert config.codex_mode == DEFAULT_CODEX_MODE
        assert config.codex_model == DEFAULT_CODEX_MODEL
        assert config.codex_reasoning_effort == DEFAULT_CODEX_REASONING_EFFORT

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

    def test_claude_config_from_env(self):
        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_CLAUDE_MODEL": "opus",
                "ALEPH_SUB_QUERY_CLAUDE_EFFORT": "low",
            },
            clear=True,
        ):
            config = SubQueryConfig()

        assert config.claude_model == "opus"
        assert config.claude_effort == "low"

    def test_programmatic_claude_config_beats_env(self):
        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_CLAUDE_MODEL": "sonnet",
                "ALEPH_SUB_QUERY_CLAUDE_EFFORT": "medium",
            },
            clear=True,
        ):
            config = SubQueryConfig(
                claude_model="opus",
                claude_effort="low",
            )

        assert config.claude_model == "opus"
        assert config.claude_effort == "low"

    def test_codex_mcp_config_from_env(self):
        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_CODEX_MODE": "mcp",
                "ALEPH_SUB_QUERY_CODEX_MODEL": "gpt-5.4",
                "ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT": "low",
                "ALEPH_SUB_QUERY_CODEX_PROFILE": "subquery",
            },
            clear=True,
        ):
            config = SubQueryConfig()

        assert config.codex_mode == "mcp"
        assert config.codex_model == "gpt-5.4"
        assert config.codex_reasoning_effort == "low"
        assert config.codex_profile == "subquery"

    def test_programmatic_codex_config_beats_env(self):
        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_CODEX_MODE": "mcp",
                "ALEPH_SUB_QUERY_CODEX_MODEL": "gpt-5.4",
                "ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT": "low",
                "ALEPH_SUB_QUERY_CODEX_PROFILE": "subquery",
            },
            clear=True,
        ):
            config = SubQueryConfig(
                codex_mode="exec",
                codex_model="custom-model",
                codex_reasoning_effort="medium",
                codex_profile="custom-profile",
            )

        assert config.codex_mode == "exec"
        assert config.codex_model == "custom-model"
        assert config.codex_reasoning_effort == "medium"
        assert config.codex_profile == "custom-profile"

    def test_timeout_config_from_env(self):
        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_TIMEOUT": "42.5",
            },
            clear=True,
        ):
            config = SubQueryConfig()

        assert config.cli_timeout_seconds == 42.5
        assert config.api_timeout_seconds == 42.5

    def test_invalid_timeout_env_is_ignored(self):
        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_TIMEOUT": "not-a-number",
            },
            clear=True,
        ):
            config = SubQueryConfig()

        assert config.cli_timeout_seconds == 180.0
        assert config.api_timeout_seconds == 120.0


class TestDetectBackend:
    """Tests for backend detection.

    Priority order (Codex-first):
    1. ALEPH_SUB_QUERY_BACKEND env var (explicit override)
    2. codex CLI (if installed)
    3. API fallback (will error with helpful message)
    """

    def test_detect_backend_cli_preferred_with_aleph_key(self):
        """CLI should be preferred even when ALEPH_SUB_QUERY_API_KEY is set."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_API_KEY": "test-key"}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
                assert detect_backend() == "codex"

    def test_detect_backend_cli_preferred_with_openai_key(self):
        """CLI should be preferred even when OPENAI_API_KEY is set (fallback)."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
                assert detect_backend() == "codex"

    def test_detect_backend_explicit_override(self):
        """ALEPH_SUB_QUERY_BACKEND should override all other detection."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "codex", "ALEPH_SUB_QUERY_API_KEY": "key"}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
                mock_which.return_value = "/usr/bin/something"
                assert detect_backend() == "codex"

    def test_detect_backend_explicit_override_api(self):
        """ALEPH_SUB_QUERY_BACKEND=api should force API even without credentials."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "api"}, clear=True):
            assert detect_backend() == "api"

    def test_detect_backend_respects_programmatic_config(self):
        """SubQueryConfig.backend should override env auto-detection."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.config.shutil.which", return_value="/usr/bin/codex"):
                assert detect_backend(SubQueryConfig(backend="api")) == "api"

    def test_detect_backend_explicit_override_claude(self):
        """Claude stays available when explicitly selected."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "claude"}, clear=True):
            assert detect_backend() == "claude"

    def test_detect_backend_explicit_override_gemini(self):
        """Gemini stays available when explicitly selected."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "gemini"}, clear=True):
            assert detect_backend() == "gemini"

    def test_detect_backend_does_not_auto_select_claude(self):
        """Auto mode should fall back to API instead of selecting Claude."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/claude" if x == "claude" else None
                assert detect_backend() == "api"

    def test_detect_backend_codex_when_available(self):
        """Codex CLI should be used when available."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/codex" if x == "codex" else None
                assert detect_backend() == "codex"

    def test_detect_backend_does_not_auto_select_gemini(self):
        """Auto mode should fall back to API instead of selecting Gemini."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
                mock_which.side_effect = lambda x: "/usr/bin/gemini" if x == "gemini" else None
                assert detect_backend() == "api"

    def test_detect_backend_api_fallback(self):
        """API fallback when nothing else available (will error with helpful message)."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
                mock_which.return_value = None
                assert detect_backend() == "api"

    def test_detect_backend_model_override_does_not_beat_cli(self):
        """ALEPH_SUB_QUERY_MODEL should not override CLI preference."""
        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_MODEL": "gpt-5.2-codex", "OPENAI_API_KEY": "key"}, clear=True):
            with patch("aleph.sub_query.config.shutil.which") as mock_which:
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
    async def test_cli_nonzero_exit_with_stdout_returns_failure(self):
        """Non-zero exit code should return failure even if stdout has content."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"echoed prompt text", b"some error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="claude",
                timeout=10.0,
            )
            assert success is False
            assert "CLI error" in output

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

    @pytest.mark.asyncio
    async def test_claude_cli_extracts_result_from_json_output(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b'{"result":"Claude response"}', b"")
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="claude",
            )

        assert success is True
        assert output == "Claude response"

    @pytest.mark.asyncio
    async def test_claude_cli_disables_session_persistence(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"result":"Claude response"}', b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="claude",
            )

        assert success is True
        assert output == "Claude response"
        cmd = mock_exec.call_args.args
        assert "--no-session-persistence" in cmd

    @pytest.mark.asyncio
    async def test_claude_cli_passes_model_and_effort(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"result":"Claude response"}', b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="claude",
                claude_model="opus",
                claude_effort="low",
            )

        assert success is True
        assert output == "Claude response"
        cmd = mock_exec.call_args.args
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "opus"
        assert "--effort" in cmd
        assert cmd[cmd.index("--effort") + 1] == "low"

    @pytest.mark.asyncio
    async def test_claude_cli_uses_env_model_and_effort_defaults(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"result":"Claude response"}', b""))

        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_CLAUDE_MODEL": "sonnet",
                "ALEPH_SUB_QUERY_CLAUDE_EFFORT": "medium",
            },
            clear=True,
        ):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                success, output = await run_cli_sub_query(
                    prompt="test prompt",
                    backend="claude",
                )

        assert success is True
        assert output == "Claude response"
        cmd = mock_exec.call_args.args
        assert cmd[cmd.index("--model") + 1] == "sonnet"
        assert cmd[cmd.index("--effort") + 1] == "medium"

    @pytest.mark.asyncio
    async def test_gemini_cli_extracts_response_from_json_output(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        payload = (
            b"startup noise\n"
            b'{"session_id":"abc","response":"Gemini response","stats":{}}'
        )
        mock_proc.communicate = AsyncMock(return_value=(payload, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="gemini",
            )

        assert success is True
        assert output == "Gemini response"

    @pytest.mark.asyncio
    async def test_gemini_cli_disables_sandbox_by_default(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Gemini response", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="gemini",
            )

        assert success is True
        assert output == "Gemini response"
        cmd = mock_exec.call_args.args
        assert cmd[:9] == (
            "gemini",
            "-y",
            "--sandbox=false",
            "--extensions",
            "",
            "-o",
            "json",
            "-p",
            "test prompt",
        )

    @pytest.mark.asyncio
    async def test_gemini_cli_disables_extensions(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Gemini response", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            success, output = await run_cli_sub_query(
                prompt="test prompt",
                backend="gemini",
                mcp_server_url="http://127.0.0.1:8765/mcp",
                mcp_server_name="aleph_shared",
            )

        assert success is True
        assert output == "Gemini response"
        cmd = mock_exec.call_args.args
        assert "--extensions" in cmd
        idx = cmd.index("--extensions")
        assert cmd[idx + 1] == ""

    @pytest.mark.asyncio
    async def test_gemini_cli_respects_sandbox_env_override(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Gemini response", b""))

        with patch.dict(os.environ, {"ALEPH_SUB_QUERY_GEMINI_SANDBOX": "true"}, clear=True):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                success, output = await run_cli_sub_query(
                    prompt="test prompt",
                    backend="gemini",
                )

        assert success is True
        assert output == "Gemini response"
        cmd = mock_exec.call_args.args
        assert cmd[:9] == (
            "gemini",
            "-y",
            "--sandbox=true",
            "--extensions",
            "",
            "-o",
            "json",
            "-p",
            "test prompt",
        )

    @pytest.mark.asyncio
    async def test_gemini_tempfile_mode_disables_sandbox_by_default(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Gemini response", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            success, output = await run_cli_sub_query(
                prompt="P" * 12_000,
                backend="gemini",
            )

        assert success is True
        assert output == "Gemini response"
        cmd = mock_exec.call_args.args
        assert cmd[:9] == (
            "gemini",
            "-y",
            "--sandbox=false",
            "--extensions",
            "",
            "-o",
            "json",
            "-p",
            "",
        )

    @pytest.mark.asyncio
    async def test_cli_context_slice_respects_max_context_chars(self):
        with patch(
            "aleph.sub_query.cli_backend.run_codex_mcp_sub_query",
            new=AsyncMock(return_value=(True, "OK", "thread-123")),
        ) as mock_codex_mcp:
            success, output = await run_cli_sub_query(
                prompt="Summarize this:",
                context_slice="ABCDEFGHIJ",
                backend="codex",
                max_context_chars=4,
            )
            assert success is True
            assert output == "OK"
            mock_codex_mcp.assert_awaited_once()
            assert mock_codex_mcp.await_args.kwargs["context_slice"] == "ABCD"

    @pytest.mark.asyncio
    async def test_cli_with_shared_mcp_does_not_embed_context_slice(self):
        with patch(
            "aleph.sub_query.cli_backend.run_codex_mcp_sub_query",
            new=AsyncMock(return_value=(True, "OK", "thread-123")),
        ) as mock_codex_mcp:
            success, output = await run_cli_sub_query(
                prompt="Summarize this",
                context_slice="VERY_SECRET_CONTEXT",
                backend="codex",
                mcp_server_url="http://127.0.0.1:8765/mcp",
            )
            assert success is True
            assert output == "OK"
            mock_codex_mcp.assert_awaited_once()
            kwargs = mock_codex_mcp.await_args.kwargs
            assert kwargs["prompt"] == "Summarize this"
            assert kwargs["context_slice"] == "VERY_SECRET_CONTEXT"
            assert kwargs["mcp_server_url"] == "http://127.0.0.1:8765/mcp"

    @pytest.mark.asyncio
    async def test_codex_mcp_mode_routes_to_codex_mcp_backend(self):
        with patch.dict(
            os.environ,
            {
                "ALEPH_SUB_QUERY_CODEX_MODE": "mcp",
                "ALEPH_SUB_QUERY_CODEX_MODEL": "gpt-5.4",
                "ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT": "low",
            },
            clear=True,
        ):
            with patch(
                "aleph.sub_query.cli_backend.run_codex_mcp_sub_query",
                new=AsyncMock(return_value=(True, "OK", "thread-123")),
            ) as mock_codex_mcp:
                success, output = await run_cli_sub_query(
                    prompt="Summarize this",
                    context_slice="VERY_SECRET_CONTEXT",
                    backend="codex",
                    mcp_server_url="http://127.0.0.1:8765/mcp",
                    mcp_server_name="aleph_shared",
                )

        assert success is True
        assert output == "OK"
        mock_codex_mcp.assert_awaited_once()
        kwargs = mock_codex_mcp.await_args.kwargs
        assert kwargs["prompt"] == "Summarize this"
        assert kwargs["context_slice"] == "VERY_SECRET_CONTEXT"
        assert kwargs["mcp_server_url"] == "http://127.0.0.1:8765/mcp"
        assert kwargs["mcp_server_name"] == "aleph_shared"
        assert kwargs["model"] == "gpt-5.4"
        assert kwargs["reasoning_effort"] == "low"

    @pytest.mark.asyncio
    async def test_codex_defaults_to_mcp_backend(self):
        with patch(
            "aleph.sub_query.cli_backend.run_codex_mcp_sub_query",
            new=AsyncMock(return_value=(True, "OK", "thread-123")),
        ) as mock_codex_mcp:
            success, output = await run_cli_sub_query(
                prompt="Summarize this",
                context_slice="VERY_SECRET_CONTEXT",
                backend="codex",
            )

        assert success is True
        assert output == "OK"
        mock_codex_mcp.assert_awaited_once()
        kwargs = mock_codex_mcp.await_args.kwargs
        assert kwargs["model"] == DEFAULT_CODEX_MODEL
        assert kwargs["reasoning_effort"] == DEFAULT_CODEX_REASONING_EFFORT


class TestCodexMcpBackend:
    def test_compose_sub_query_prompt_embeds_context_even_with_shared_session(self):
        prompt = compose_sub_query_prompt(
            "Summarize this patent chunk",
            "VERY_SECRET_CONTEXT",
        )

        assert prompt == (
            "Summarize this patent chunk\n\n---\nContext:\nVERY_SECRET_CONTEXT"
        )

    def test_build_codex_tool_call_uses_reply_thread(self):
        tool_name, arguments = build_codex_mcp_tool_call(
            prompt="Continue",
            thread_id="thread-123",
        )

        assert tool_name == "codex-reply"
        assert arguments == {
            "prompt": "Continue",
            "threadId": "thread-123",
        }

    def test_extract_codex_result_from_jsonable_payload(self):
        result = {
            "structuredContent": {
                "threadId": "thread-123",
                "content": "OK",
            },
            "content": [
                {"type": "text", "text": "OK"},
            ],
        }

        output, thread_id = extract_codex_mcp_result_text(result)

        assert output == "OK"
        assert thread_id == "thread-123"


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
    async def test_api_context_slice_respects_max_context_chars(self):
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
                    context_slice="ABCDEFGHIJ",
                    max_context_chars=4,
                )
                assert success is True
                assert output == "Response"

                call_args = mock_instance.post.call_args
                payload = call_args.kwargs.get("json", call_args.args[1] if len(call_args.args) > 1 else {})
                messages = payload.get("messages", [])
                user_message = next(m.get("content", "") for m in messages if m.get("role") == "user")
                assert "ABCD" in user_message
                assert "ABCDE" not in user_message

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

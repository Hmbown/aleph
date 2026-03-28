"""Tests for the llama.cpp provider."""

from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aleph.providers.llamacpp import LlamaCppProvider
from aleph.providers.base import ProviderError
from aleph.providers.registry import get_provider


# ---------------------------------------------------------------------------
# Instantiation & registry
# ---------------------------------------------------------------------------


class TestLlamaCppInit:
    def test_registry_lookup(self):
        provider = get_provider("llamacpp")
        assert provider.provider_name == "llamacpp"

    def test_registry_case_insensitive(self):
        provider = get_provider("LlamaCpp")
        assert provider.provider_name == "llamacpp"

    def test_default_url(self):
        p = LlamaCppProvider()
        assert p._base_url == "http://127.0.0.1:8080"

    def test_custom_url(self):
        p = LlamaCppProvider(base_url="http://localhost:9999")
        assert p._base_url == "http://localhost:9999"

    def test_env_url(self, monkeypatch):
        monkeypatch.setenv("ALEPH_LLAMACPP_URL", "http://10.0.0.1:5555")
        p = LlamaCppProvider()
        assert p._base_url == "http://10.0.0.1:5555"

    def test_explicit_url_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ALEPH_LLAMACPP_URL", "http://10.0.0.1:5555")
        p = LlamaCppProvider(base_url="http://custom:1234")
        assert p._base_url == "http://custom:1234"

    def test_model_path_from_env(self, monkeypatch):
        monkeypatch.setenv("ALEPH_LLAMACPP_MODEL", "/tmp/test.gguf")
        p = LlamaCppProvider()
        assert p._model_path == "/tmp/test.gguf"

    def test_context_size_from_env(self, monkeypatch):
        monkeypatch.setenv("ALEPH_LLAMACPP_CTX", "32768")
        p = LlamaCppProvider()
        assert p._context_size == 32768

    def test_gpu_layers_from_env(self, monkeypatch):
        monkeypatch.setenv("ALEPH_LLAMACPP_GPU_LAYERS", "40")
        p = LlamaCppProvider()
        assert p._gpu_layers == 40

    def test_auto_start_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("ALEPH_LLAMACPP_AUTO_START", "false")
        p = LlamaCppProvider()
        assert p._auto_start is False

    def test_api_key_ignored(self):
        p = LlamaCppProvider(api_key="sk-should-be-ignored")
        assert p.provider_name == "llamacpp"

    def test_cost_always_zero(self):
        p = LlamaCppProvider()
        assert p._estimate_cost("any-model", 1000, 500) == 0.0


# ---------------------------------------------------------------------------
# Protocol methods
# ---------------------------------------------------------------------------


class TestLlamaCppProtocol:
    def test_count_tokens(self):
        p = LlamaCppProvider()
        n = p.count_tokens("hello world", "local")
        assert isinstance(n, int)
        assert n > 0

    def test_context_limit_default(self):
        p = LlamaCppProvider(context_size=16384)
        assert p.get_context_limit("local") == 16384

    def test_context_limit_from_props(self):
        p = LlamaCppProvider(context_size=8192)
        p._server_props = {
            "default_generation_settings": {"n_ctx": 32768},
        }
        assert p.get_context_limit("local") == 32768

    def test_output_limit(self):
        p = LlamaCppProvider(context_size=8192)
        limit = p.get_output_limit("local")
        assert limit == 4096  # min(8192//2, 4096)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    async def test_healthy_server(self):
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get = AsyncMock(return_value=mock_resp)

        p = LlamaCppProvider(http_client=mock_client)
        assert await p._health_check() is True

    async def test_unhealthy_server(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        p = LlamaCppProvider(http_client=mock_client)
        assert await p._health_check() is False


# ---------------------------------------------------------------------------
# Completion (mocked HTTP)
# ---------------------------------------------------------------------------


class TestComplete:
    async def test_basic_completion(self):
        """Mock a successful /v1/chat/completions response."""
        api_response = {
            "choices": [
                {"message": {"role": "assistant", "content": "Hello from Qwen!"}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))

        p = LlamaCppProvider(http_client=mock_client, auto_start=False)
        p._server_ready = True  # skip server check

        with patch(
            "aleph.providers.llamacpp.post_json_with_retries",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            text, inp, out, cost = await p.complete(
                messages=[{"role": "user", "content": "Hi"}],
                model="local",
            )

        assert text == "Hello from Qwen!"
        assert inp == 10
        assert out == 5
        assert cost == 0.0

    async def test_no_choices_raises(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": []}

        p = LlamaCppProvider(auto_start=False)
        p._server_ready = True

        with patch(
            "aleph.providers.llamacpp.post_json_with_retries",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ), pytest.raises(ProviderError, match="no choices"):
            await p.complete(
                messages=[{"role": "user", "content": "Hi"}],
                model="local",
            )

    async def test_stop_sequences_passed(self):
        api_response = {
            "choices": [
                {"message": {"role": "assistant", "content": "done"}}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response

        captured_payload = {}

        async def capture_post(**kwargs):
            captured_payload.update(kwargs.get("payload", {}))
            return mock_resp

        p = LlamaCppProvider(auto_start=False)
        p._server_ready = True

        with patch(
            "aleph.providers.llamacpp.post_json_with_retries",
            side_effect=capture_post,
        ):
            await p.complete(
                messages=[{"role": "user", "content": "x"}],
                model="local",
                stop_sequences=["FINAL("],
            )

        assert captured_payload.get("stop") == ["FINAL("]


# ---------------------------------------------------------------------------
# Server auto-start
# ---------------------------------------------------------------------------


class TestAutoStart:
    async def test_no_model_path_raises(self):
        p = LlamaCppProvider(auto_start=True)
        p._model_path = None

        with pytest.raises(ProviderError, match="No model path"):
            await p._start_server()

    async def test_missing_model_file_raises(self):
        p = LlamaCppProvider(auto_start=True, model_path="/nonexistent/model.gguf")

        with pytest.raises(ProviderError, match="not found"):
            await p._start_server()

    async def test_missing_binary_raises(self):
        p = LlamaCppProvider(auto_start=True, model_path="/tmp/fake.gguf")

        with patch("pathlib.Path.exists", return_value=True), \
             patch("shutil.which", return_value=None):
            with pytest.raises(ProviderError, match="llama-server not found"):
                await p._start_server()

    async def test_ensure_server_skips_when_ready(self):
        p = LlamaCppProvider(auto_start=False)
        p._server_ready = True

        # Should return immediately without any network calls.
        await p._ensure_server()

    async def test_ensure_server_not_running_no_autostart(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        p = LlamaCppProvider(http_client=mock_client, auto_start=False)

        with pytest.raises(ProviderError, match="not running"):
            await p._ensure_server()


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_config_base_url_passthrough(self):
        from aleph.config import AlephConfig

        cfg = AlephConfig(
            provider="llamacpp",
            root_model="local",
            base_url="http://myhost:9090",
        )
        assert cfg.base_url == "http://myhost:9090"

    def test_config_env_base_url(self, monkeypatch):
        from aleph.config import AlephConfig

        monkeypatch.setenv("ALEPH_PROVIDER", "llamacpp")
        monkeypatch.setenv("ALEPH_BASE_URL", "http://envhost:7070")
        monkeypatch.setenv("ALEPH_MODEL", "local")

        cfg = AlephConfig.from_env()
        assert cfg.provider == "llamacpp"
        assert cfg.base_url == "http://envhost:7070"

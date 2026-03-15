"""llama.cpp provider.

Local LLM inference via llama-server's OpenAI-compatible API.

llama-server (from the llama.cpp project) exposes ``/v1/chat/completions``
that is wire-compatible with the OpenAI Chat API.  This provider talks to
that endpoint, optionally auto-starting the server if it isn't already
running.

Install llama.cpp:
    Mac:     brew install llama.cpp
    Windows: winget install ggml.LlamaCpp
    Linux:   https://github.com/ggml-org/llama.cpp#build

Environment variables:
    ALEPH_LLAMACPP_URL          Server URL (default http://127.0.0.1:8080)
    ALEPH_LLAMACPP_MODEL        Path to .gguf model file (for auto-start)
    ALEPH_LLAMACPP_CTX          Context size in tokens (default 8192)
    ALEPH_LLAMACPP_GPU_LAYERS   Layers to offload to GPU (default 99 = all)
    ALEPH_LLAMACPP_AUTO_START   Auto-start server if not running (default true)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .base import ProviderError
from .http_base import BaseHTTPProvider
from .http_utils import post_json_with_retries
from ..utils.tokens import estimate_tokens
from ..types import Message

log = logging.getLogger(__name__)


class LlamaCppProvider(BaseHTTPProvider):
    """Local LLM provider via llama.cpp's llama-server."""

    # No MODEL_INFO — models are local with user-defined limits.
    DEFAULT_CONTEXT_LIMIT = 8192
    DEFAULT_OUTPUT_LIMIT = 4096

    def __init__(
        self,
        api_key: str | None = None,  # accepted for interface compat, ignored
        base_url: str | None = None,
        model_path: str | None = None,
        context_size: int | None = None,
        gpu_layers: int | None = None,
        auto_start: bool | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.5,
    ) -> None:
        _url = base_url or os.getenv("ALEPH_LLAMACPP_URL", "http://127.0.0.1:8080")
        self._model_path = model_path or os.getenv("ALEPH_LLAMACPP_MODEL")
        self._context_size = context_size or int(os.getenv("ALEPH_LLAMACPP_CTX", "8192"))
        self._gpu_layers = gpu_layers if gpu_layers is not None else int(
            os.getenv("ALEPH_LLAMACPP_GPU_LAYERS", "99")
        )
        if auto_start is not None:
            self._auto_start = auto_start
        else:
            self._auto_start = os.getenv("ALEPH_LLAMACPP_AUTO_START", "true").lower() in {
                "1", "true", "yes",
            }

        self._server_process: subprocess.Popen[bytes] | None = None
        self._server_ready = False
        self._server_props: dict[str, object] | None = None

        super().__init__(
            api_key="not-needed",
            api_key_env="ALEPH_LLAMACPP_API_KEY",  # never actually used
            base_url=_url,
            http_client=http_client,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
        )

    # ------------------------------------------------------------------
    # Provider protocol
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "llamacpp"

    def count_tokens(self, text: str, model: str) -> int:
        return estimate_tokens(text)

    def get_context_limit(self, model: str) -> int:
        if self._server_props:
            dgs = self._server_props.get("default_generation_settings")
            if isinstance(dgs, dict):
                n_ctx = dgs.get("n_ctx")
                if isinstance(n_ctx, int) and n_ctx > 0:
                    return n_ctx
        return self._context_size

    def get_output_limit(self, model: str) -> int:
        # llama-server doesn't advertise a separate output limit.
        return min(self._context_size // 2, 4096)

    # ------------------------------------------------------------------
    # Server management
    # ------------------------------------------------------------------

    async def _health_check(self) -> bool:
        """Return True if llama-server is healthy."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self._base_url}/health", timeout=httpx.Timeout(3.0),
            )
            return resp.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            return False

    async def _fetch_props(self) -> dict[str, object]:
        """Fetch ``/props`` for context size and model metadata."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self._base_url}/props", timeout=httpx.Timeout(5.0),
            )
            if resp.status_code == 200:
                self._server_props = resp.json()
                return self._server_props or {}
        except Exception:
            pass
        return {}

    async def _start_server(self) -> None:
        """Launch ``llama-server`` as a subprocess."""
        if not self._model_path:
            raise ProviderError(
                "No model path configured for llama.cpp auto-start. "
                "Set ALEPH_LLAMACPP_MODEL=/path/to/model.gguf "
                "or pass model_path= to LlamaCppProvider."
            )
        model = Path(self._model_path)
        if not model.exists():
            raise ProviderError(f"Model file not found: {self._model_path}")

        server_bin = shutil.which("llama-server")
        if not server_bin:
            raise ProviderError(
                "llama-server not found on PATH. Install llama.cpp:\n"
                "  Mac:     brew install llama.cpp\n"
                "  Windows: winget install ggml.LlamaCpp\n"
                "  Linux:   https://github.com/ggml-org/llama.cpp#build"
            )

        parsed = urlparse(self._base_url)
        host = parsed.hostname or "127.0.0.1"
        port = str(parsed.port or 8080)

        cmd = [
            server_bin,
            "-m", str(model),
            "-c", str(self._context_size),
            "-ngl", str(self._gpu_layers),
            "--host", host,
            "--port", port,
        ]
        log.info("Starting llama-server: %s", " ".join(cmd))

        self._server_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Wait up to 120 s for the server to be ready (large models need time).
        for _ in range(120):
            if self._server_process.poll() is not None:
                raise ProviderError(
                    f"llama-server exited immediately (code {self._server_process.returncode}). "
                    f"Check that the model is valid: {self._model_path}"
                )
            if await self._health_check():
                log.info("llama-server ready on %s:%s", host, port)
                return
            await asyncio.sleep(1.0)

        # Timed out — kill and report.
        self._server_process.terminate()
        raise ProviderError(
            "llama-server failed to become healthy within 120 s. "
            "Try starting it manually to see error output."
        )

    async def _ensure_server(self) -> None:
        """Make sure the server is running; start it if needed."""
        if self._server_ready:
            return

        if await self._health_check():
            await self._fetch_props()
            self._server_ready = True
            return

        if not self._auto_start:
            raise ProviderError(
                f"llama-server is not running at {self._base_url}. "
                "Start it manually or set ALEPH_LLAMACPP_AUTO_START=true."
            )

        await self._start_server()
        await self._fetch_props()
        self._server_ready = True

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        stop_sequences: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[str, int, int, float]:
        await self._ensure_server()

        url = f"{self._base_url}/v1/chat/completions"
        headers = {"content-type": "application/json"}

        payload: dict[str, object] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop_sequences:
            payload["stop"] = stop_sequences

        # Use a generous default timeout for local inference.
        if timeout_seconds is None:
            timeout_seconds = 300.0
        client, timeout = await self._get_timeout(timeout_seconds)

        resp = await post_json_with_retries(
            client=client,
            url=url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            max_retries=self._max_retries,
            backoff_base_seconds=self._backoff_base,
            provider_label="llama.cpp",
            request_id_headers=(),
        )

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Invalid JSON from llama-server: {exc}")

        if not isinstance(data, dict):
            raise ProviderError(f"llama-server returned unexpected JSON type: {type(data)}")

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("llama-server returned no choices")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            message = {}
        text = (message.get("content") or "").strip()

        # Some models (e.g. Qwen 3.5) expose chain-of-thought in a separate
        # ``reasoning_content`` field.  If the main content is empty but
        # reasoning is present, fall back to reasoning so the RLM loop still
        # gets usable text.
        if not text:
            reasoning = (message.get("reasoning_content") or "").strip()
            if reasoning:
                text = reasoning
                log.debug("Using reasoning_content as response (content was empty)")

        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)

        if input_tokens == 0:
            input_tokens = sum(
                estimate_tokens(m.get("content", "")) for m in messages
            )
        if output_tokens == 0:
            output_tokens = estimate_tokens(text)

        return text, input_tokens, output_tokens, 0.0  # local = free

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Shut down the HTTP client and any managed server process."""
        await super().aclose()
        if self._server_process is not None:
            log.info("Stopping llama-server (pid %d)", self._server_process.pid)
            self._server_process.terminate()
            try:
                self._server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._server_process.kill()
            self._server_process = None
        self._server_ready = False

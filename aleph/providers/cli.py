"""CLI provider.

Routes Aleph root calls through local CLI tools (claude/codex/gemini).
"""

from __future__ import annotations

import os
from typing import Literal, cast

from .base import ProviderError
from ..sub_query import detect_backend
from ..sub_query.cli_backend import CLI_BACKENDS, run_cli_sub_query
from ..types import Message
from ..utils.tokens import estimate_tokens


CLIBackend = Literal["claude", "codex", "gemini"]

DEFAULT_CONTEXT_LIMIT = 200_000
DEFAULT_OUTPUT_LIMIT = 8_192
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_OUTPUT_CHARS = 50_000


def _get_env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _get_env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


class CLIProvider:
    """LLM provider that uses local CLI tools."""

    def __init__(
        self,
        backend: str | None = None,
        timeout_seconds: float | None = None,
        max_output_chars: int | None = None,
        api_key: str | None = None,
    ) -> None:
        self._backend_setting = backend or os.environ.get("ALEPH_CLI_BACKEND", "auto")
        self._timeout_seconds = timeout_seconds or _get_env_float(
            "ALEPH_CLI_TIMEOUT",
            _get_env_float("ALEPH_SUB_QUERY_TIMEOUT", DEFAULT_TIMEOUT_SECONDS),
        )
        self._max_output_chars = max_output_chars or _get_env_int(
            "ALEPH_CLI_MAX_OUTPUT_CHARS",
            DEFAULT_MAX_OUTPUT_CHARS,
        )
        self._api_key = api_key

    @property
    def provider_name(self) -> str:
        return "cli"

    def _resolve_backend(self, model: str | None = None) -> CLIBackend:
        value = self._backend_setting
        if model is not None:
            candidate = model.strip().lower()
            if candidate in CLI_BACKENDS or candidate == "auto":
                value = candidate

        key = value.strip().lower()
        if key == "auto":
            detected = detect_backend()
            if detected == "api":
                raise ProviderError(
                    "No CLI backend detected. Install claude/codex/gemini or set ALEPH_CLI_BACKEND."
                )
            return cast(CLIBackend, detected)
        if key in CLI_BACKENDS:
            return cast(CLIBackend, key)
        allowed = ", ".join(sorted(CLI_BACKENDS))
        raise ProviderError(f"Unsupported CLI backend: {value}. Choose from: {allowed}.")

    def count_tokens(self, text: str, model: str) -> int:
        return estimate_tokens(text)

    def get_context_limit(self, model: str) -> int:
        return DEFAULT_CONTEXT_LIMIT

    def get_output_limit(self, model: str) -> int:
        return DEFAULT_OUTPUT_LIMIT

    def _format_messages(self, messages: list[Message]) -> str:
        parts: list[str] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            label = "USER"
            if role == "system":
                label = "SYSTEM"
            elif role == "assistant":
                label = "ASSISTANT"
            parts.append(f"{label}:\n{content}")
        parts.append("ASSISTANT:")
        return "\n\n".join(parts)

    async def complete(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        stop_sequences: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[str, int, int, float]:
        _ = max_tokens, temperature, stop_sequences
        backend = self._resolve_backend(model=model)
        prompt = self._format_messages(messages)
        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        success, output = await run_cli_sub_query(
            prompt=prompt,
            context_slice=None,
            backend=backend,
            timeout=timeout,
            max_output_chars=self._max_output_chars,
        )
        if not success:
            raise ProviderError(output)
        input_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
        output_tokens = estimate_tokens(output)
        return output, input_tokens, output_tokens, 0.0

"""OpenAI provider.

Implements Aleph's provider interface against OpenAI's Chat Completions API.

This module uses bare HTTP via httpx for minimal dependencies.
"""

from __future__ import annotations

import json
import os
import httpx

from .base import ModelPricing, ProviderError
from .http_base import BaseHTTPProvider
from .http_utils import post_json_with_retries
from ..utils.tokens import estimate_tokens, try_count_tokens_tiktoken
from ..types import Message


class OpenAIProvider(BaseHTTPProvider):
    """OpenAI provider via /v1/chat/completions."""

    MODEL_INFO: dict[str, ModelPricing] = {
        # NOTE: Prices/limits change; these defaults are for budgeting/telemetry.
        "gpt-4o": ModelPricing(128_000, 16_384, 0.0025, 0.01),
        "gpt-4o-mini": ModelPricing(128_000, 16_384, 0.00015, 0.0006),
        "gpt-4-turbo": ModelPricing(128_000, 4_096, 0.01, 0.03),
    }
    DEFAULT_CONTEXT_LIMIT = 128_000
    DEFAULT_OUTPUT_LIMIT = 4_096

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com",
        organization: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.8,
    ) -> None:
        super().__init__(
            api_key=api_key,
            api_key_env="OPENAI_API_KEY",
            base_url=base_url,
            http_client=http_client,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
        )
        self._org = organization or os.getenv("OPENAI_ORG_ID")

    @property
    def provider_name(self) -> str:
        return "openai"

    def count_tokens(self, text: str, model: str) -> int:
        # Best-effort: use tiktoken if installed.
        n = try_count_tokens_tiktoken(text, model)
        if n is not None:
            return n
        return estimate_tokens(text)

    async def complete(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        stop_sequences: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[str, int, int, float]:
        if not self._api_key:
            raise ProviderError("OpenAI API key not set. Provide api_key=... or set OPENAI_API_KEY.")

        url = f"{self._base_url}/v1/chat/completions"
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        if self._org:
            headers["openai-organization"] = self._org

        # Newer OpenAI models (o1+, gpt-4.1+, gpt-5+) require
        # max_completion_tokens instead of the legacy max_tokens param.
        _new_api_prefixes = ("o1", "o3", "o4", "gpt-4.1", "gpt-4o-", "gpt-5")
        use_new_param = any(model.startswith(p) for p in _new_api_prefixes)
        token_key = "max_completion_tokens" if use_new_param else "max_tokens"

        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            token_key: max_tokens,
            "temperature": temperature,
        }
        if stop_sequences:
            payload["stop"] = stop_sequences

        client, timeout = await self._get_timeout(timeout_seconds)

        resp = await post_json_with_retries(
            client=client,
            url=url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            max_retries=self._max_retries,
            backoff_base_seconds=self._backoff_base,
            provider_label="OpenAI",
            request_id_headers=("x-request-id",),
        )

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise ProviderError(f"Invalid JSON response from OpenAI: {e}")

        if not isinstance(data, dict):
            raise ProviderError(f"OpenAI API returned invalid JSON type: {type(data)}")

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("OpenAI API returned no choices")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            message = {}
        text = (message.get("content") or "").strip()

        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)

        if input_tokens == 0:
            input_tokens = sum(self.count_tokens(m.get("content", ""), model) for m in messages)
        if output_tokens == 0:
            output_tokens = self.count_tokens(text, model)

        cost = self._estimate_cost(model, input_tokens, output_tokens)
        return text, input_tokens, output_tokens, cost

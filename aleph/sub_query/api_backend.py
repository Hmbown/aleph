"""API backend for sub-queries.

Supports OpenAI-compatible chat completions endpoints.

Configuration via environment variables:
- ALEPH_SUB_QUERY_API_KEY: API key (fallback: OPENAI_API_KEY)
- ALEPH_SUB_QUERY_URL: Base URL (fallback: OPENAI_BASE_URL, default: https://api.openai.com/v1)
- ALEPH_SUB_QUERY_MODEL: Model name (required)
"""

from __future__ import annotations

import os
from typing import Any

from . import (
    DEFAULT_API_BASE_URL_ENV,
    DEFAULT_API_KEY_ENV,
    DEFAULT_API_MODEL_ENV,
    DEFAULT_OPENAI_BASE_URL,
)

__all__ = ["run_api_sub_query"]
DEFAULT_MAX_CONTEXT_CHARS = 20_000


def _get_api_key(api_key_env: str) -> str | None:
    return os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")


def _get_base_url(api_base_url_env: str) -> str:
    return (
        os.environ.get(api_base_url_env)
        or os.environ.get("OPENAI_BASE_URL")
        or DEFAULT_OPENAI_BASE_URL
    )


def _get_model(api_model_env: str) -> str | None:
    return os.environ.get(api_model_env)


async def _call_openai_compatible(
    messages: list[dict[str, Any]],
    model: str,
    api_key: str,
    base_url: str,
    timeout: float,
    max_tokens: int,
) -> tuple[bool, str]:
    """Call OpenAI-compatible chat completions API.

    Works with: OpenAI, Groq, Together, Mistral, DeepSeek, local LLMs, etc.
    """
    try:
        import httpx
    except ImportError:
        return False, "httpx not installed. Run: pip install httpx"

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )

            if resp.status_code != 200:
                try:
                    err_data = resp.json()
                    err_msg = err_data.get("error", {}).get("message", resp.text)
                except Exception:
                    err_msg = resp.text[:500]
                return False, f"API error {resp.status_code}: {err_msg}"

            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return True, text

        except httpx.TimeoutException:
            return False, f"API timeout after {timeout}s"
        except httpx.ConnectError as e:
            return False, f"API connection error: {e}. Check ALEPH_SUB_QUERY_URL."
        except (KeyError, IndexError) as e:
            return False, f"Failed to parse API response: {e}"
        except Exception as e:
            return False, f"API request failed: {e}"


async def run_api_sub_query(
    prompt: str,
    context_slice: str | None = None,
    model: str | None = None,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    api_base_url_env: str = DEFAULT_API_BASE_URL_ENV,
    api_model_env: str = DEFAULT_API_MODEL_ENV,
    timeout: float = 60.0,
    system_prompt: str | None = None,
    max_tokens: int = 8192,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> tuple[bool, str]:
    """Run sub-query via OpenAI-compatible API.

    Configuration via environment:
    - ALEPH_SUB_QUERY_API_KEY: API key (fallback: OPENAI_API_KEY)
    - ALEPH_SUB_QUERY_URL: Custom endpoint (fallback: OPENAI_BASE_URL)
    - ALEPH_SUB_QUERY_MODEL: Required model name

    Args:
        prompt: The question/task for the sub-agent.
        context_slice: Optional context to include.
        model: Model name (required if ALEPH_SUB_QUERY_MODEL is not set).
        api_key_env: Env var name for API key.
        api_base_url_env: Env var name for API base URL.
        api_model_env: Env var name for API model.
        timeout: Request timeout in seconds.
        system_prompt: Optional system prompt.
        max_tokens: Maximum tokens in response.

    Returns:
        Tuple of (success, output).
    """
    api_key = _get_api_key(api_key_env)
    if not api_key:
        return False, (
            "No API key found. Set ALEPH_SUB_QUERY_API_KEY (preferred) or OPENAI_API_KEY."
        )

    if model is None:
        model = _get_model(api_model_env)
    if not model:
        return False, (
            "No model configured. Set ALEPH_SUB_QUERY_MODEL or pass model=..."
        )

    base_url = _get_base_url(api_base_url_env)

    if context_slice and max_context_chars > 0 and len(context_slice) > max_context_chars:
        context_slice = context_slice[:max_context_chars]

    # Build the full prompt
    full_prompt = prompt
    if context_slice:
        full_prompt = f"{prompt}\n\n---\nContext:\n{context_slice}"

    # Build messages
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": full_prompt})

    return await _call_openai_compatible(
        messages=messages,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_tokens=max_tokens,
    )

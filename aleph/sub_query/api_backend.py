"""API backend for sub-queries.

Uses OpenAI-compatible API format.
Default: Xiaomi MiMo Flash V2 (free public beta until Jan 20, 2026)
API docs: https://xiaomimimo.com
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["run_api_sub_query"]

# Default Mimo API base URL
DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"


async def run_api_sub_query(
    prompt: str,
    context_slice: str | None = None,
    model: str = "mimo-v2-flash",
    api_key_env: str = "MIMO_API_KEY",
    api_base_url_env: str = "OPENAI_BASE_URL",
    timeout: float = 60.0,
    system_prompt: str | None = None,
    max_tokens: int = 8192,
) -> tuple[bool, str]:
    """Run sub-query via OpenAI-compatible API.
    
    Default: Xiaomi MiMo Flash V2 (free public beta until Jan 20, 2026)
    
    Args:
        prompt: The question/task for the sub-agent.
        context_slice: Optional context to include.
        model: Model name.
        api_key_env: Environment variable for API key (MIMO_API_KEY or OPENAI_API_KEY).
        api_base_url_env: Environment variable for base URL.
        timeout: Request timeout in seconds.
        system_prompt: Optional system prompt.
        max_tokens: Maximum tokens in response.
    
    Returns:
        Tuple of (success, output).
    """
    # Try MIMO_API_KEY first, fall back to OPENAI_API_KEY
    api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get(api_base_url_env, DEFAULT_MIMO_BASE_URL)
    
    if not api_key:
        return False, (
            "API key not found. Set MIMO_API_KEY (or OPENAI_API_KEY) for MiMo Flash V2.\n"
            "Get a FREE key at: https://xiaomimimo.com\n"
            "Or install a CLI backend (claude, codex, aider) for no-API-key usage."
        )
    
    # Build the full prompt
    full_prompt = prompt
    if context_slice:
        full_prompt = f"{prompt}\n\n---\nContext:\n{context_slice}"
    
    # Build messages
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": full_prompt})
    
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
    
    # Import httpx lazily to avoid hard dependency
    try:
        import httpx
    except ImportError:
        return False, "httpx not installed. Run: pip install httpx"
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            
            if resp.status_code != 200:
                # Try to extract error message
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
            return False, f"API connection error: {e}. Check OPENAI_BASE_URL."
        except (KeyError, IndexError) as e:
            return False, f"Failed to parse API response: {e}"
        except Exception as e:
            return False, f"API request failed: {e}"

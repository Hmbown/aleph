"""Sub-query module for RLM-style recursive reasoning.

This module enables Aleph to spawn sub-agents that can reason over context slices,
following the Recursive Language Model (RLM) paradigm.

Backend priority (configurable via ALEPH_SUB_QUERY_BACKEND):
1. CLI backends (codex, gemini) - uses existing subscriptions
   Note: claude CLI is deprioritized as it hangs in MCP/sandbox contexts
2. API (if credentials available) - OpenAI-compatible APIs only (last resort)

Configuration via environment:
- ALEPH_SUB_QUERY_API_KEY (or OPENAI_API_KEY fallback)
- ALEPH_SUB_QUERY_URL (or OPENAI_BASE_URL fallback, default: https://api.openai.com/v1)
- ALEPH_SUB_QUERY_MODEL (required)
- ALEPH_SUB_QUERY_TIMEOUT (seconds, applies to CLI + API sub-queries)
- ALEPH_SUB_QUERY_SHARE_SESSION (share live MCP session with CLI sub-agents)
- ALEPH_SUB_QUERY_HTTP_HOST / ALEPH_SUB_QUERY_HTTP_PORT / ALEPH_SUB_QUERY_HTTP_PATH
- ALEPH_SUB_QUERY_MCP_SERVER_NAME (server name exposed to sub-agents)
- ALEPH_SUB_QUERY_VALIDATION_REGEX (optional regex for strict output validation)
- ALEPH_SUB_QUERY_MAX_RETRIES (retry count after validation failure)
- ALEPH_SUB_QUERY_RETRY_PROMPT (retry prompt suffix)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "SubQueryConfig",
    "detect_backend",
    "DEFAULT_CONFIG",
    "has_api_credentials",
]


BackendType = Literal["claude", "codex", "gemini", "api", "auto"]

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_API_KEY_ENV = "ALEPH_SUB_QUERY_API_KEY"
DEFAULT_API_BASE_URL_ENV = "ALEPH_SUB_QUERY_URL"
DEFAULT_API_MODEL_ENV = "ALEPH_SUB_QUERY_MODEL"
DEFAULT_TIMEOUT_ENV = "ALEPH_SUB_QUERY_TIMEOUT"


@dataclass
class SubQueryConfig:
    """Configuration for sub-query backend.

    The backend priority can be configured via environment variables:

    - ALEPH_SUB_QUERY_BACKEND: Force a specific backend ("api", "claude", "codex", "gemini", "auto")
    - ALEPH_SUB_QUERY_API_KEY: API key for OpenAI-compatible providers (fallback: OPENAI_API_KEY)
    - ALEPH_SUB_QUERY_URL: Base URL for OpenAI-compatible APIs (fallback: OPENAI_BASE_URL)
    - ALEPH_SUB_QUERY_MODEL: Model name (required)
    - ALEPH_SUB_QUERY_TIMEOUT: Timeout in seconds for CLI/API backends
    - ALEPH_SUB_QUERY_SHARE_SESSION: Share live MCP session with CLI sub-agents
    - ALEPH_SUB_QUERY_HTTP_HOST / ALEPH_SUB_QUERY_HTTP_PORT / ALEPH_SUB_QUERY_HTTP_PATH
    - ALEPH_SUB_QUERY_MCP_SERVER_NAME: Server name exposed to sub-agents

    When backend="auto" (default), the priority is:
    1. codex CLI - if installed
    2. gemini CLI - if installed
    3. claude CLI - if installed (deprioritized: hangs in MCP/sandbox contexts)
    4. API - if credentials are available (fallback)

    Attributes:
        backend: Which backend to use. "auto" prioritizes CLI, then API.
        cli_timeout_seconds: Timeout for CLI subprocess calls.
        cli_max_output_chars: Maximum output characters from CLI.
        api_timeout_seconds: Timeout for API calls.
        api_key_env: Environment variable name for API key.
        api_base_url_env: Environment variable name for API base URL.
        api_model_env: Environment variable name for API model.
        api_model: Explicit model override (if provided programmatically).
        max_context_chars: Truncate context slices longer than this.
        include_system_prompt: Whether to include a system prompt for sub-queries.
        validation_regex: Optional regex to validate sub-query output.
        max_retries: Number of retries after a validation failure.
        retry_prompt: Prompt suffix used when retrying after validation failure.
    """

    backend: BackendType = "auto"

    # CLI options
    cli_timeout_seconds: float = 120.0
    cli_max_output_chars: int = 50_000

    # API options
    api_timeout_seconds: float = 60.0
    api_key_env: str = DEFAULT_API_KEY_ENV
    api_base_url_env: str = DEFAULT_API_BASE_URL_ENV
    api_model_env: str = DEFAULT_API_MODEL_ENV
    api_model: str | None = None

    # Behavior
    max_context_chars: int = 100_000
    include_system_prompt: bool = True
    validation_regex: str | None = None
    max_retries: int = 0
    retry_prompt: str = (
        "The previous output did not match the required format. "
        "Respond again and match the required format exactly."
    )

    # System prompt for sub-queries
    system_prompt: str = field(
        default="""You are a focused sub-agent processing a single task. This is a one-shot operation.

INSTRUCTIONS:
1. Answer the question based ONLY on the provided context
2. Be concise - provide direct answers without preamble
3. If context is insufficient, say "INSUFFICIENT_CONTEXT: [what's missing]"
4. Structure your response for easy parsing:
   - For summaries: bullet points or numbered lists
   - For extractions: key: value format
   - For analysis: clear sections with headers
5. Do not make up information not present in the context

OUTPUT FORMAT:
- Start directly with your answer (no "Based on the context..." preamble)
- End with a confidence indicator if uncertain: [CONFIDENCE: high/medium/low]"""
    )

    def __post_init__(self) -> None:
        timeout_env = os.environ.get(DEFAULT_TIMEOUT_ENV, "").strip()
        if not timeout_env:
            return
        try:
            timeout_val = float(timeout_env)
        except ValueError:
            return
        if timeout_val <= 0:
            return
        self.cli_timeout_seconds = timeout_val
        self.api_timeout_seconds = timeout_val


def _get_api_key(api_key_env: str) -> str | None:
    """Return API key from explicit env var or OPENAI_API_KEY fallback."""
    return os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")


def has_api_credentials(config: SubQueryConfig | None = None) -> bool:
    """Check if API credentials are available for the sub-query backend."""
    cfg = config or DEFAULT_CONFIG
    return _get_api_key(cfg.api_key_env) is not None


def detect_backend(config: SubQueryConfig | None = None) -> BackendType:
    """Auto-detect the best available backend.

    Priority (CLI-first; API is last resort):
    1. Check ALEPH_SUB_QUERY_BACKEND env var for explicit override
    2. codex CLI - if installed
    3. gemini CLI - if installed
    4. claude CLI - if installed (deprioritized: hangs in MCP/sandbox contexts)
    5. api (fallback) - will error if no credentials, but gives helpful message

    Returns:
        The detected backend type.
    """
    # Check for explicit backend override
    explicit_backend = os.environ.get("ALEPH_SUB_QUERY_BACKEND", "").lower().strip()
    if explicit_backend in ("api", "claude", "codex", "gemini"):
        return explicit_backend  # type: ignore

    # Priority 2-4: CLI backends (codex/gemini preferred over claude)
    # Note: claude CLI hangs in MCP/sandbox contexts, so it's deprioritized
    if shutil.which("codex"):
        return "codex"
    if shutil.which("gemini"):
        return "gemini"
    if shutil.which("claude"):
        return "claude"

    # Fallback to API (will error with helpful message if no credentials)
    return "api"


DEFAULT_CONFIG = SubQueryConfig()

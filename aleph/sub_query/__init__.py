"""Sub-query backends and configuration for recursive reasoning."""

from .config import (
    DEFAULT_API_BASE_URL_ENV,
    DEFAULT_API_KEY_ENV,
    DEFAULT_API_MODEL_ENV,
    DEFAULT_CLAUDE_EFFORT,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_MODE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_CONFIG,
    DEFAULT_OPENAI_BASE_URL,
    SubQueryConfig,
    detect_backend,
    has_api_credentials,
)

__all__ = [
    "SubQueryConfig",
    "detect_backend",
    "DEFAULT_CONFIG",
    "DEFAULT_CLAUDE_MODEL",
    "DEFAULT_CLAUDE_EFFORT",
    "DEFAULT_CODEX_MODE",
    "DEFAULT_CODEX_MODEL",
    "DEFAULT_CODEX_REASONING_EFFORT",
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_API_BASE_URL_ENV",
    "DEFAULT_API_MODEL_ENV",
    "DEFAULT_OPENAI_BASE_URL",
    "has_api_credentials",
]

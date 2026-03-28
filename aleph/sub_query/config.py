"""Configuration and backend selection policy for sub-queries."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Literal

BackendType = Literal["claude", "codex", "gemini", "kimi", "api", "auto"]
CodexMode = Literal["exec", "mcp"]

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_API_KEY_ENV = "ALEPH_SUB_QUERY_API_KEY"
DEFAULT_API_BASE_URL_ENV = "ALEPH_SUB_QUERY_URL"
DEFAULT_API_MODEL_ENV = "ALEPH_SUB_QUERY_MODEL"
DEFAULT_TIMEOUT_ENV = "ALEPH_SUB_QUERY_TIMEOUT"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_EFFORT = "low"
DEFAULT_CODEX_MODE: CodexMode = "mcp"
DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CODEX_REASONING_EFFORT = "low"

_CLAUDE_MODEL_ENV = "ALEPH_SUB_QUERY_CLAUDE_MODEL"
_CLAUDE_EFFORT_ENV = "ALEPH_SUB_QUERY_CLAUDE_EFFORT"
_CODEX_MODE_ENV = "ALEPH_SUB_QUERY_CODEX_MODE"
_CODEX_MODEL_ENV = "ALEPH_SUB_QUERY_CODEX_MODEL"
_CODEX_REASONING_ENV = "ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT"
_CODEX_PROFILE_ENV = "ALEPH_SUB_QUERY_CODEX_PROFILE"


def _env_text(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def default_claude_model() -> str:
    return _env_text(_CLAUDE_MODEL_ENV) or DEFAULT_CLAUDE_MODEL


def default_claude_effort() -> str:
    return _env_text(_CLAUDE_EFFORT_ENV) or DEFAULT_CLAUDE_EFFORT


def default_codex_mode() -> CodexMode:
    return normalize_codex_mode(_env_text(_CODEX_MODE_ENV))


def default_codex_model() -> str:
    return _env_text(_CODEX_MODEL_ENV) or DEFAULT_CODEX_MODEL


def default_codex_reasoning_effort() -> str:
    return _env_text(_CODEX_REASONING_ENV) or DEFAULT_CODEX_REASONING_EFFORT


def default_codex_profile() -> str | None:
    return _env_text(_CODEX_PROFILE_ENV)


def resolve_claude_model(value: str | None) -> str:
    return value or default_claude_model()


def resolve_claude_effort(value: str | None) -> str:
    return value or default_claude_effort()


def normalize_codex_mode(value: str | None) -> CodexMode:
    normalized = (value or DEFAULT_CODEX_MODE).lower()
    if normalized in {"mcp", "server"}:
        return "mcp"
    return "exec"


def resolve_codex_mode(value: str | None) -> CodexMode:
    return normalize_codex_mode(value or _env_text(_CODEX_MODE_ENV))


def resolve_codex_model(value: str | None) -> str | None:
    resolved = value or default_codex_model()
    return resolved.strip() if isinstance(resolved, str) and resolved.strip() else None


def resolve_codex_reasoning_effort(value: str | None) -> str | None:
    resolved = value or default_codex_reasoning_effort()
    return resolved.strip() if isinstance(resolved, str) and resolved.strip() else None


def resolve_codex_profile(value: str | None) -> str | None:
    resolved = value or default_codex_profile()
    return resolved.strip() if isinstance(resolved, str) and resolved.strip() else None


@dataclass
class SubQueryConfig:
    """Configuration for sub-query backend."""

    backend: BackendType = "auto"

    # CLI options
    cli_timeout_seconds: float = 180.0
    cli_max_output_chars: int = 50_000

    # API options
    api_timeout_seconds: float = 120.0
    api_key_env: str = DEFAULT_API_KEY_ENV
    api_base_url_env: str = DEFAULT_API_BASE_URL_ENV
    api_model_env: str = DEFAULT_API_MODEL_ENV
    api_model: str | None = None

    # Behavior
    max_context_chars: int = 20_000
    include_system_prompt: bool = True
    validation_regex: str | None = None
    max_retries: int = 0
    claude_model: str | None = None
    claude_effort: str | None = None
    codex_mode: CodexMode | None = None
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None
    codex_profile: str | None = None
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
        if self.claude_model is None:
            self.claude_model = default_claude_model()

        if self.claude_effort is None:
            self.claude_effort = default_claude_effort()

        if self.codex_mode is None:
            self.codex_mode = default_codex_mode()

        if self.codex_model is None:
            self.codex_model = default_codex_model()

        if self.codex_reasoning_effort is None:
            self.codex_reasoning_effort = default_codex_reasoning_effort()

        if self.codex_profile is None:
            self.codex_profile = default_codex_profile()

        timeout_env = _env_text(DEFAULT_TIMEOUT_ENV)
        if timeout_env is None:
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
    """Auto-detect the best available backend."""
    cfg = config or DEFAULT_CONFIG

    if cfg.backend != "auto":
        return cfg.backend

    explicit_backend = os.environ.get("ALEPH_SUB_QUERY_BACKEND", "").lower().strip()
    if explicit_backend in ("api", "claude", "codex", "gemini", "kimi"):
        return explicit_backend  # type: ignore[return-value]

    if shutil.which("codex"):
        return "codex"

    return "api"


DEFAULT_CONFIG = SubQueryConfig()

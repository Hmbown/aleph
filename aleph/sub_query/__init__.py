"""Sub-query module for RLM-style recursive reasoning.

This module enables Aleph to spawn sub-agents that can reason over context slices,
following the Recursive Language Model (RLM) paradigm.

Backend priority:
1. CLI backends (claude, codex, aider) - no API key needed
2. API fallback (Mimo Flash V2 by default) - uses OpenAI-compatible format
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Literal

__all__ = ["SubQueryConfig", "detect_backend", "DEFAULT_CONFIG"]


BackendType = Literal["claude", "codex", "aider", "api", "auto"]


@dataclass
class SubQueryConfig:
    """Configuration for sub-query backend.
    
    Attributes:
        backend: Which backend to use. "auto" will try CLI first, then API.
        cli_timeout_seconds: Timeout for CLI subprocess calls.
        cli_max_output_chars: Maximum output characters from CLI.
        api_base_url_env: Environment variable for API base URL.
        api_key_env: Environment variable for API key.
        api_model: Model name for API calls.
        max_context_chars: Truncate context slices longer than this.
        include_system_prompt: Whether to include a system prompt for sub-queries.
    """
    backend: BackendType = "auto"
    
    # CLI options
    cli_timeout_seconds: float = 120.0
    cli_max_output_chars: int = 50_000
    
    # API fallback options
    # Default: Xiaomi MiMo Flash V2 (free public beta until Jan 20, 2026)
    # Uses OpenAI-compatible API format at api.xiaomimimo.com
    api_base_url_env: str = "OPENAI_BASE_URL"
    api_key_env: str = "MIMO_API_KEY"  # Falls back to OPENAI_API_KEY
    api_model: str = "mimo-v2-flash"
    api_timeout_seconds: float = 60.0
    
    # Behavior
    max_context_chars: int = 100_000
    include_system_prompt: bool = True
    
    # System prompt for sub-queries
    system_prompt: str = field(default="""You are a focused sub-agent analyzing a specific portion of a larger document.
Your task is to answer the question based ONLY on the provided context.
Be concise and precise. If the context doesn't contain enough information to answer, say so.
Do not make up information not present in the context.""")


def detect_backend() -> BackendType:
    """Auto-detect the best available backend.
    
    Priority:
    1. claude CLI (Claude Code)
    2. codex CLI (OpenAI Codex)
    3. aider CLI
    4. api (Mimo Flash V2 or other OpenAI-compatible)
    
    Returns:
        The detected backend type.
    """
    if shutil.which("claude"):
        return "claude"
    if shutil.which("codex"):
        return "codex"
    if shutil.which("aider"):
        return "aider"
    return "api"


def has_api_credentials(config: SubQueryConfig | None = None) -> bool:
    """Check if API credentials are available (MIMO_API_KEY or OPENAI_API_KEY)."""
    cfg = config or DEFAULT_CONFIG
    return bool(os.environ.get(cfg.api_key_env) or os.environ.get("OPENAI_API_KEY"))


DEFAULT_CONFIG = SubQueryConfig()

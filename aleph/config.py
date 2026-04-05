"""Configuration management for Aleph.

AlephConfig can be instantiated directly, loaded from env vars, or loaded from a
YAML/JSON config file.

The goal is to make it easy to go from *configuration* -> a ready-to-run Aleph
instance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from .types import Budget
from .repl.sandbox import DEFAULT_ALLOWED_IMPORTS, SandboxConfig
from .providers.registry import get_provider
from .core import Aleph
from .settings import AlephEnvSettings


@dataclass(slots=True)
class AlephConfig:
    """Complete configuration for an Aleph instance."""

    # Provider / models
    provider: str = "anthropic"
    root_model: str = "claude-sonnet-4-20250514"
    sub_model: str | None = None
    api_key: str | None = None
    base_url: str | None = None  # Override provider's default base URL

    # Budget defaults
    max_tokens: int | None = None
    max_iterations: int = 100
    max_depth: int = 2
    max_wall_time_seconds: float = 300.0
    max_sub_queries: int = 100

    # Sandbox
    enable_code_execution: bool = True
    allowed_imports: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_IMPORTS))
    sandbox_timeout_seconds: float = 180.0
    max_output_chars: int = 50_000
    unrestricted_sandbox: bool = False  # Bypass all sandbox restrictions

    # REPL
    context_var_name: str = "ctx"

    # Caching
    enable_caching: bool = True
    cache_backend: Literal["memory"] = "memory"

    # Observability
    log_trajectory: bool = True
    log_level: str = "INFO"

    # Custom prompt
    system_prompt: str | None = None

    # RLM output feedback mode: "full" (default) or "metadata" (paper-aligned)
    output_feedback: str = "full"

    # Swarm mode settings
    # Enable with ALEPH_SWARM_MODE=true or --swarm-mode flag
    swarm_mode: bool = False
    # Enable session sharing between sub-agents in swarm mode
    # ALEPH_SWARM_SESSION_SHARING=true
    swarm_session_sharing: bool = True
    # Maximum concurrent agents in swarm
    swarm_max_agents: int = 10
    # Context ID prefix for swarm sessions (e.g., "swarm" -> "swarm-agent-1")
    swarm_context_prefix: str = "swarm"
    # Swarm identifier for coordination (ALEPH_SWARM_NAME)
    swarm_name: str | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "AlephConfig":
        """Load config from YAML or JSON."""

        path = Path(path)
        content = path.read_text(encoding="utf-8")

        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except Exception as e:  # pragma: no cover
                raise RuntimeError(
                    "YAML support requires PyYAML. Install aleph[yaml] or `pip install pyyaml`."
                ) from e
            data = yaml.safe_load(content) or {}
        else:
            data = json.loads(content) if content.strip() else {}

        if not isinstance(data, dict):
            raise ValueError(f"Config file must parse to an object/dict, got: {type(data)}")
        return cls(**cast(dict[str, Any], data))

    @classmethod
    def from_env(cls) -> "AlephConfig":
        """Load config from environment variables."""
        settings = AlephEnvSettings()
        return cls(
            provider=settings.provider,
            root_model=settings.root_model,
            sub_model=settings.sub_model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            max_tokens=settings.max_tokens,
            max_iterations=settings.max_iterations,
            max_depth=settings.max_depth,
            max_wall_time_seconds=settings.max_wall_time_seconds,
            max_sub_queries=settings.max_sub_queries,
            enable_caching=settings.enable_caching,
            log_trajectory=settings.log_trajectory,
            output_feedback=settings.output_feedback,
            swarm_mode=settings.swarm_mode,
            swarm_session_sharing=settings.swarm_session_sharing,
            swarm_max_agents=settings.swarm_max_agents,
            swarm_context_prefix=settings.swarm_context_prefix,
            swarm_name=settings.swarm_name,
            unrestricted_sandbox=settings.unrestricted_sandbox,
        )

    def to_budget(self) -> Budget:
        """Convert this config to a :class:`~aleph.types.Budget` instance."""
        return Budget(
            max_tokens=self.max_tokens,
            max_iterations=self.max_iterations,
            max_depth=self.max_depth,
            max_wall_time_seconds=self.max_wall_time_seconds,
            max_sub_queries=self.max_sub_queries,
        )

    def to_sandbox_config(self) -> SandboxConfig:
        """Convert this config to a :class:`~aleph.repl.sandbox.SandboxConfig` instance."""
        return SandboxConfig(
            allowed_imports=self.allowed_imports,
            max_output_chars=self.max_output_chars,
            timeout_seconds=self.sandbox_timeout_seconds,
            enable_code_execution=self.enable_code_execution,
            unrestricted=self.unrestricted_sandbox,
        )


def create_aleph(config: AlephConfig | Mapping[str, object] | str | Path | None = None) -> Aleph:
    """Factory to create Aleph from config sources."""

    if config is None:
        cfg = AlephConfig.from_env()
    elif isinstance(config, AlephConfig):
        cfg = config
    elif isinstance(config, Mapping):
        cfg = AlephConfig(**cast(dict[str, Any], dict(config)))
    elif isinstance(config, (str, Path)):
        cfg = AlephConfig.from_file(config)
    else:
        raise TypeError(f"Invalid config type: {type(config)}")

    # Provider instance
    provider_kwargs: dict[str, object] = {"api_key": cfg.api_key}
    if cfg.base_url:
        provider_kwargs["base_url"] = cfg.base_url
    provider = get_provider(cfg.provider, **provider_kwargs)

    return Aleph(
        provider=provider,
        root_model=cfg.root_model,
        sub_model=cfg.sub_model or cfg.root_model,
        budget=cfg.to_budget(),
        sandbox_config=cfg.to_sandbox_config(),
        system_prompt=cfg.system_prompt,
        enable_caching=cfg.enable_caching,
        log_trajectory=cfg.log_trajectory,
        output_feedback=cfg.output_feedback,
    )

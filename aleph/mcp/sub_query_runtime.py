"""Pure helpers for sub-query runtime configuration inside the MCP server."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from ..settings import SubQueryEnvSettings
from ..sub_query import SubQueryConfig, detect_backend
from ..sub_query.config import (
    resolve_codex_mode,
    resolve_codex_model,
    resolve_codex_profile,
    resolve_codex_reasoning_effort,
)


def get_sub_query_config_snapshot(
    config: SubQueryConfig,
    *,
    context_policy: str,
) -> dict[str, Any]:
    env_settings = SubQueryEnvSettings()
    backend_env = (env_settings.backend or "").strip().lower()
    configured_backend = getattr(config, "backend", "auto")
    if configured_backend and configured_backend != "auto":
        backend_display = configured_backend
    else:
        backend_display = backend_env or configured_backend or "auto"
    return {
        "sub_query_backend": backend_display,
        "sub_query_backend_resolved": detect_backend(config),
        "sub_query_timeout_seconds": {
            "cli": config.cli_timeout_seconds,
            "api": config.api_timeout_seconds,
        },
        "sub_query_share_session": (
            env_settings.share_session if env_settings.share_session is not None else False
        ),
        "sub_query_claude": {
            "model": config.claude_model,
            "effort": config.claude_effort,
        },
        "sub_query_codex": {
            "mode": resolve_codex_mode(config.codex_mode),
            "model": resolve_codex_model(config.codex_model),
            "reasoning_effort": resolve_codex_reasoning_effort(config.codex_reasoning_effort),
            "profile": resolve_codex_profile(config.codex_profile),
        },
        "context_policy": context_policy,
    }


def apply_sub_query_runtime_config(
    config: SubQueryConfig,
    *,
    cli_backends: Iterable[str],
    sub_query_backend: str | None = None,
    sub_query_timeout: float | None = None,
    sub_query_share_session: bool | None = None,
) -> tuple[bool, str]:
    allowed_backends = {"auto", "api", *cli_backends}

    if sub_query_backend is not None:
        backend = sub_query_backend.strip().lower()
        if backend not in allowed_backends:
            allowed_list = ", ".join(sorted(allowed_backends))
            return False, f"Unsupported backend '{sub_query_backend}'. Choose from: {allowed_list}."
        os.environ["ALEPH_SUB_QUERY_BACKEND"] = backend
        config.backend = backend  # type: ignore[assignment]

    if sub_query_timeout is not None:
        if sub_query_timeout <= 0:
            return False, "sub_query_timeout must be greater than 0."
        config.cli_timeout_seconds = sub_query_timeout
        config.api_timeout_seconds = sub_query_timeout
        os.environ["ALEPH_SUB_QUERY_TIMEOUT"] = str(sub_query_timeout)

    if sub_query_share_session is not None:
        os.environ["ALEPH_SUB_QUERY_SHARE_SESSION"] = (
            "true" if sub_query_share_session else "false"
        )

    return True, "Configuration updated."


def apply_sub_query_cli_env_overrides(
    *,
    sub_query_backend: str | None = None,
    sub_query_timeout: float | None = None,
    sub_query_share_session: bool | None = None,
    sub_query_api_model: str | None = None,
    sub_query_claude_model: str | None = None,
    sub_query_claude_effort: str | None = None,
    sub_query_codex_mode: str | None = None,
    sub_query_codex_model: str | None = None,
    sub_query_codex_reasoning_effort: str | None = None,
    sub_query_codex_profile: str | None = None,
    context_policy: str | None = None,
) -> None:
    overrides = {
        "ALEPH_SUB_QUERY_BACKEND": sub_query_backend,
        "ALEPH_SUB_QUERY_TIMEOUT": (
            str(sub_query_timeout) if sub_query_timeout is not None else None
        ),
        "ALEPH_SUB_QUERY_SHARE_SESSION": (
            "true" if sub_query_share_session else "false"
            if sub_query_share_session is not None
            else None
        ),
        "ALEPH_SUB_QUERY_MODEL": sub_query_api_model,
        "ALEPH_SUB_QUERY_CLAUDE_MODEL": sub_query_claude_model,
        "ALEPH_SUB_QUERY_CLAUDE_EFFORT": sub_query_claude_effort,
        "ALEPH_SUB_QUERY_CODEX_MODE": sub_query_codex_mode,
        "ALEPH_SUB_QUERY_CODEX_MODEL": sub_query_codex_model,
        "ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT": sub_query_codex_reasoning_effort,
        "ALEPH_SUB_QUERY_CODEX_PROFILE": sub_query_codex_profile,
        "ALEPH_CONTEXT_POLICY": context_policy,
    }
    for name, value in overrides.items():
        if value is not None:
            os.environ[name] = value

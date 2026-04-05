"""Typed environment-backed settings models used across Aleph.

These models centralize env-var names and coercion while preserving Aleph's
existing forgiving behavior for invalid values.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


def _strip_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_optional_int(value: object) -> int | None:
    text = _strip_optional_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _parse_int(value: object, *, default: int) -> int:
    parsed = _parse_optional_int(value)
    return default if parsed is None else parsed


def _parse_optional_float(value: object) -> float | None:
    text = _strip_optional_text(value)
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_float(value: object, *, default: float) -> float:
    parsed = _parse_optional_float(value)
    return default if parsed is None else parsed


def _parse_bool(value: object, *, default: bool | None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


class _AlephBaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        env_ignore_empty=True,
    )


class AlephEnvSettings(_AlephBaseSettings):
    provider: str = Field(
        default="anthropic",
        validation_alias=AliasChoices("ALEPH_PROVIDER", "RLM_PROVIDER"),
    )
    root_model: str = Field(
        default="claude-sonnet-4-20250514",
        validation_alias=AliasChoices("ALEPH_MODEL", "RLM_MODEL"),
    )
    sub_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALEPH_SUB_MODEL", "RLM_SUB_MODEL"),
    )
    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALEPH_API_KEY", "RLM_API_KEY"),
    )
    base_url: str | None = Field(default=None, validation_alias="ALEPH_BASE_URL")
    max_tokens: int | None = Field(default=None, validation_alias="ALEPH_MAX_TOKENS")
    max_iterations: int = Field(default=100, validation_alias="ALEPH_MAX_ITERATIONS")
    max_depth: int = Field(default=2, validation_alias="ALEPH_MAX_DEPTH")
    max_wall_time_seconds: float = Field(default=300.0, validation_alias="ALEPH_MAX_WALL_TIME")
    max_sub_queries: int = Field(default=100, validation_alias="ALEPH_MAX_SUB_QUERIES")
    enable_caching: bool = Field(default=True, validation_alias="ALEPH_ENABLE_CACHING")
    log_trajectory: bool = Field(default=True, validation_alias="ALEPH_LOG_TRAJECTORY")
    output_feedback: str = Field(default="full", validation_alias="ALEPH_OUTPUT_FEEDBACK")
    swarm_mode: bool = Field(default=False, validation_alias="ALEPH_SWARM_MODE")
    swarm_session_sharing: bool = Field(default=True, validation_alias="ALEPH_SWARM_SESSION_SHARING")
    swarm_max_agents: int = Field(default=10, validation_alias="ALEPH_SWARM_MAX_AGENTS")
    swarm_context_prefix: str = Field(default="swarm", validation_alias="ALEPH_SWARM_CONTEXT_PREFIX")
    swarm_name: str | None = Field(default=None, validation_alias="ALEPH_SWARM_NAME")
    unrestricted_sandbox: bool = Field(default=False, validation_alias="ALEPH_UNRESTRICTED_SANDBOX")

    @field_validator(
        "provider",
        "root_model",
        "sub_model",
        "api_key",
        "base_url",
        "output_feedback",
        "swarm_context_prefix",
        "swarm_name",
        mode="before",
    )
    @classmethod
    def _strip_text_fields(cls, value: object) -> str | None:
        return _strip_optional_text(value)

    @field_validator("max_tokens", mode="before")
    @classmethod
    def _coerce_optional_int(cls, value: object) -> int | None:
        return _parse_optional_int(value)

    @field_validator("max_iterations", mode="before")
    @classmethod
    def _coerce_max_iterations(cls, value: object) -> int:
        return _parse_int(value, default=100)

    @field_validator("max_depth", mode="before")
    @classmethod
    def _coerce_max_depth(cls, value: object) -> int:
        return _parse_int(value, default=2)

    @field_validator("max_sub_queries", mode="before")
    @classmethod
    def _coerce_max_sub_queries(cls, value: object) -> int:
        return _parse_int(value, default=100)

    @field_validator("swarm_max_agents", mode="before")
    @classmethod
    def _coerce_swarm_max_agents(cls, value: object) -> int:
        return _parse_int(value, default=10)

    @field_validator("max_wall_time_seconds", mode="before")
    @classmethod
    def _coerce_wall_time(cls, value: object) -> float:
        return _parse_float(value, default=300.0)

    @field_validator(
        "enable_caching",
        "log_trajectory",
        "swarm_mode",
        "swarm_session_sharing",
        "unrestricted_sandbox",
        mode="before",
    )
    @classmethod
    def _coerce_bool_fields(cls, value: object, info: ValidationInfo) -> bool:
        defaults = {
            "enable_caching": True,
            "log_trajectory": True,
            "swarm_mode": False,
            "swarm_session_sharing": True,
            "unrestricted_sandbox": False,
        }
        field_name = info.field_name or ""
        default = defaults.get(field_name, False)
        parsed = _parse_bool(value, default=default)
        assert parsed is not None
        return parsed


class SubQueryEnvSettings(_AlephBaseSettings):
    backend: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_BACKEND")
    timeout_seconds: float | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_TIMEOUT")
    model: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_MODEL")
    claude_model: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_CLAUDE_MODEL")
    claude_effort: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_CLAUDE_EFFORT")
    codex_mode: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_CODEX_MODE")
    codex_model: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_CODEX_MODEL")
    codex_reasoning_effort: str | None = Field(
        default=None,
        validation_alias="ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT",
    )
    codex_profile: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_CODEX_PROFILE")
    share_session: bool | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_SHARE_SESSION")
    validation_regex: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_VALIDATION_REGEX")
    max_retries: int | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_MAX_RETRIES")
    retry_prompt: str | None = Field(default=None, validation_alias="ALEPH_SUB_QUERY_RETRY_PROMPT")
    http_host: str = Field(default="127.0.0.1", validation_alias="ALEPH_SUB_QUERY_HTTP_HOST")
    http_port: int = Field(default=8765, validation_alias="ALEPH_SUB_QUERY_HTTP_PORT")
    http_path: str = Field(default="/mcp", validation_alias="ALEPH_SUB_QUERY_HTTP_PATH")
    mcp_server_name: str | None = Field(
        default=None,
        validation_alias="ALEPH_SUB_QUERY_MCP_SERVER_NAME",
    )

    @field_validator(
        "backend",
        "model",
        "claude_model",
        "claude_effort",
        "codex_mode",
        "codex_model",
        "codex_reasoning_effort",
        "codex_profile",
        "validation_regex",
        "retry_prompt",
        "http_host",
        "http_path",
        "mcp_server_name",
        mode="before",
    )
    @classmethod
    def _strip_fields(cls, value: object) -> str | None:
        return _strip_optional_text(value)

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _coerce_timeout(cls, value: object) -> float | None:
        return _parse_optional_float(value)

    @field_validator("max_retries", mode="before")
    @classmethod
    def _coerce_max_retries(cls, value: object) -> int | None:
        return _parse_optional_int(value)

    @field_validator("http_port", mode="before")
    @classmethod
    def _coerce_http_port(cls, value: object) -> int:
        return _parse_int(value, default=8765)

    @field_validator("share_session", mode="before")
    @classmethod
    def _coerce_share_session(cls, value: object) -> bool | None:
        return _parse_bool(value, default=None)


class MCPServerEnvSettings(_AlephBaseSettings):
    tool_docs: Literal["concise", "full"] = Field(default="concise", validation_alias="ALEPH_TOOL_DOCS")
    context_policy: str | None = Field(default=None, validation_alias="ALEPH_CONTEXT_POLICY")
    action_policy: Literal["read-write", "read-only"] = Field(
        default="read-write",
        validation_alias="ALEPH_ACTION_POLICY",
    )
    workspace_root: str | None = Field(default=None, validation_alias="ALEPH_WORKSPACE_ROOT")
    remote_tool_timeout_seconds: float = Field(
        default=120.0,
        validation_alias="ALEPH_REMOTE_TOOL_TIMEOUT",
    )
    swarm_mode: bool = Field(default=False, validation_alias="ALEPH_SWARM_MODE")
    swarm_name: str | None = Field(default=None, validation_alias="ALEPH_SWARM_NAME")
    swarm_session_sharing: bool = Field(default=True, validation_alias="ALEPH_SWARM_SESSION_SHARING")
    swarm_max_agents: int = Field(default=10, validation_alias="ALEPH_SWARM_MAX_AGENTS")
    swarm_context_prefix: str = Field(default="swarm", validation_alias="ALEPH_SWARM_CONTEXT_PREFIX")

    @field_validator("tool_docs", mode="before")
    @classmethod
    def _coerce_tool_docs(cls, value: object) -> Literal["concise", "full"]:
        text = (_strip_optional_text(value) or "concise").lower()
        if text in {"concise", "full"}:
            return text  # type: ignore[return-value]
        return "concise"

    @field_validator("action_policy", mode="before")
    @classmethod
    def _coerce_action_policy(cls, value: object) -> Literal["read-write", "read-only"]:
        text = (_strip_optional_text(value) or "read-write").lower()
        if text in {"read-write", "workspace-write", "write"}:
            return "read-write"
        if text in {"read-only", "readonly", "safe"}:
            return "read-only"
        return "read-write"

    @field_validator("context_policy", "workspace_root", "swarm_name", "swarm_context_prefix", mode="before")
    @classmethod
    def _strip_text_values(cls, value: object) -> str | None:
        return _strip_optional_text(value)

    @field_validator("remote_tool_timeout_seconds", mode="before")
    @classmethod
    def _coerce_remote_tool_timeout(cls, value: object) -> float:
        return _parse_float(value, default=120.0)

    @field_validator("swarm_max_agents", mode="before")
    @classmethod
    def _coerce_swarm_max_agents(cls, value: object) -> int:
        return _parse_int(value, default=10)

    @field_validator("swarm_mode", "swarm_session_sharing", mode="before")
    @classmethod
    def _coerce_mcp_bool_fields(cls, value: object, info: ValidationInfo) -> bool:
        default = True if info.field_name == "swarm_session_sharing" else False
        parsed = _parse_bool(value, default=default)
        assert parsed is not None
        return parsed

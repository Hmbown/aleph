"""Backward-compatible argument normalization helpers."""

from __future__ import annotations

from .types import ContentFormat

OUTPUT_FEEDBACK_MODES = ("full", "metadata")

_CONTENT_FORMAT_ALIASES: dict[str, ContentFormat] = {
    "markdown": ContentFormat.TEXT,
    "md": ContentFormat.TEXT,
}

_OUTPUT_FEEDBACK_ALIASES: dict[str, str] = {
    "minimal": "metadata",
}


def normalize_content_format(
    value: str | ContentFormat,
    *,
    allow_auto: bool = False,
) -> ContentFormat | str:
    """Normalize user-facing content format arguments."""

    if isinstance(value, ContentFormat):
        return value

    normalized = value.strip().lower()
    if allow_auto and normalized == "auto":
        return "auto"

    alias = _CONTENT_FORMAT_ALIASES.get(normalized)
    if alias is not None:
        return alias

    return ContentFormat(normalized)


def normalize_output_feedback(value: str) -> str:
    """Normalize legacy output feedback aliases to canonical modes."""

    normalized = _OUTPUT_FEEDBACK_ALIASES.get(value.strip().lower(), value.strip().lower())
    if normalized not in OUTPUT_FEEDBACK_MODES:
        raise ValueError(
            f"output_feedback must be one of {OUTPUT_FEEDBACK_MODES}, got {value!r}"
        )
    return normalized

from __future__ import annotations

from aleph.mcp.local_server import _format_payload


def test_local_server_format_payload_object_returns_raw() -> None:
    """output='object' must return the raw payload without sanitization."""
    payload = {
        "ctx": "alpha\n" * 200,
        "note": "z" * 20_000,
    }

    rendered = _format_payload(payload, output="object")

    # Raw payload — no redaction or truncation
    assert rendered["ctx"] == payload["ctx"]
    assert rendered["note"] == payload["note"]


def test_local_server_format_payload_redacts_ctx_and_truncates_large_strings() -> None:
    """json/markdown modes should sanitize (redact ctx, truncate large strings)."""
    payload = {
        "ctx": "alpha\n" * 200,
        "note": "z" * 20_000,
    }

    rendered = _format_payload(payload, output="json")

    assert "context_field_blocked" in rendered
    assert "TRUNCATED" in rendered

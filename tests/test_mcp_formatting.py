from __future__ import annotations

from aleph.mcp.local_server import _format_payload


def test_local_server_format_payload_redacts_ctx_and_truncates_large_strings() -> None:
    payload = {
        "ctx": "alpha\n" * 200,
        "note": "z" * 20_000,
    }

    rendered = _format_payload(payload, output="object")

    assert rendered["ctx"]["redacted"] is True
    assert rendered["ctx"]["reason"] == "context_field_blocked"
    assert rendered["ctx"]["original_chars"] == len(payload["ctx"])
    assert "value_preview" in rendered["ctx"]

    assert rendered["note"] != payload["note"]
    assert "TRUNCATED" in rendered["note"]

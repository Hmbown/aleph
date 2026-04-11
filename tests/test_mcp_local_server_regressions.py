"""Regression tests for Aleph MCP local server behavior."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aleph.mcp.local_server import ActionConfig, AlephMCPServerLocal
from aleph.repl.sandbox import SandboxConfig


async def _call_tool(server: AlephMCPServerLocal, tool_name: str, **kwargs: Any) -> Any:
    """Call a FastMCP tool and return the normalized result payload."""

    _, payload = await server.server.call_tool(tool_name, kwargs)
    return payload["result"]


@pytest.mark.asyncio
async def test_peek_context_line_numbers_align_with_search_results() -> None:
    server = AlephMCPServerLocal(sandbox_config=SandboxConfig(timeout_seconds=5.0))

    await _call_tool(
        server,
        "load_context",
        context="a\nb\nc",
        context_id="doc",
        line_number_base=1,
    )

    search_result = await _call_tool(server, "search_context", context_id="doc", pattern="b")
    assert "**Line 2:**" in search_result

    line_slice = await _call_tool(
        server,
        "peek_context",
        context_id="doc",
        unit="lines",
        start=2,
        end=3,
    )
    assert line_slice == "b"

    # Backward compatibility for previous 0-based callers.
    zero_based_slice = await _call_tool(
        server,
        "peek_context",
        context_id="doc",
        unit="lines",
        start=0,
        end=1,
    )
    assert zero_based_slice == "a"


@pytest.mark.asyncio
async def test_rebinding_helper_names_in_exec_python_does_not_break_tools() -> None:
    server = AlephMCPServerLocal(sandbox_config=SandboxConfig(timeout_seconds=5.0))
    await _call_tool(server, "load_context", context="alpha\nbeta\ngamma", context_id="doc")

    await _call_tool(
        server,
        "exec_python",
        context_id="doc",
        code="lines = 123\nsearch = 456\nsemantic_search = 789",
    )

    line_slice = await _call_tool(
        server,
        "peek_context",
        context_id="doc",
        unit="lines",
        start=1,
        end=2,
    )
    assert line_slice == "alpha"

    search_result = await _call_tool(server, "search_context", context_id="doc", pattern="beta")
    assert "**Line 2:**" in search_result

    semantic_result = await _call_tool(
        server,
        "semantic_search",
        context_id="doc",
        query="beta",
        top_k=1,
    )
    assert "helper is not available" not in semantic_result


@pytest.mark.asyncio
async def test_exec_python_truncates_large_ctx_return_value() -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=200)
    )
    await _call_tool(server, "load_context", context="A" * 20_000, context_id="doc")

    result = await _call_tool(server, "exec_python", context_id="doc", code="ctx")
    assert isinstance(result, str)
    assert "TRUNCATED" in result
    assert "A" * 1_000 not in result
    assert len(result) <= server.max_tool_response_chars


@pytest.mark.asyncio
async def test_reasoning_tools_public_status_and_finalize() -> None:
    server = AlephMCPServerLocal(sandbox_config=SandboxConfig(timeout_seconds=5.0))
    await _call_tool(server, "load_context", context="alpha\nbeta", context_id="doc")

    status = await _call_tool(server, "get_status", context_id="doc", output="object")
    assert status["context_id"] == "doc"
    assert status["size_lines"] == 2

    final = await _call_tool(server, "finalize", context_id="doc", answer="done", confidence="high")
    assert "## Final Answer" in final
    assert "done" in final
    assert "**Confidence:** high" in final


@pytest.mark.asyncio
async def test_finalize_auto_saves_memory_pack_in_active_workspace_repo(tmp_path: Path) -> None:
    repo = tmp_path / "active-repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(
            enabled=True,
            workspace_root=Path("/"),
            workspace_mode="any",
        ),
    )

    mock_session = AsyncMock()
    mock_session.list_roots.return_value = SimpleNamespace(
        roots=[SimpleNamespace(uri=repo.as_uri(), name=None)]
    )
    ctx = AsyncMock()
    ctx.request_context.session = mock_session

    await server._maybe_resolve_workspace_from_roots(ctx)
    await _call_tool(server, "load_context", context="alpha\nbeta", context_id="doc")
    await _call_tool(server, "finalize", context_id="doc", answer="done", confidence="high")

    assert server.action_config.workspace_root == repo
    pack_path = repo / ".aleph" / "memory_pack.json"
    assert pack_path.exists()
    payload = json.loads(pack_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "aleph.memory_pack.v1"
    assert any(session["context_id"] == "doc" for session in payload["sessions"])


@pytest.mark.asyncio
async def test_load_session_accepts_context_id_format_and_reports_skipped(tmp_path: Path) -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )
    await _call_tool(server, "load_context", context="persist me", context_id="saved")

    save_result = await _call_tool(
        server,
        "save_session",
        path="pack.json",
        confirm=True,
        output="object",
    )
    assert save_result["status"] == "success"

    server._sessions.clear()
    load_result = await _call_tool(
        server,
        "load_session",
        path="pack.json",
        confirm=True,
        output="object",
    )
    assert "saved" in load_result["loaded"]
    assert load_result["skipped"] == []

    bad_pack = tmp_path / "bad_pack.json"
    bad_pack.write_text(
        json.dumps({"schema": "aleph.memory_pack.v1", "sessions": [{"ctx": "bad"}]}),
        encoding="utf-8",
    )
    bad_result = await _call_tool(
        server,
        "load_session",
        path="bad_pack.json",
        confirm=True,
        output="object",
    )
    assert bad_result["loaded"] == []
    assert bad_result["skipped"][0]["id"] == "<missing>"

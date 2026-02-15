"""End-to-end context isolation checks for Aleph MCP tools."""

from __future__ import annotations

import json

import pytest

from aleph.core import Aleph
from aleph.mcp.local_server import ActionConfig, AlephMCPServerLocal
from aleph.prompts.system import DEFAULT_SYSTEM_PROMPT
from aleph.repl.sandbox import SandboxConfig
from aleph.types import ContentFormat, ContextMetadata


def _assert_response_bounded(result: object, max_chars: int = 10_000) -> None:
    if isinstance(result, str):
        assert len(result) <= max_chars
        return
    if isinstance(result, dict):
        assert len(json.dumps(result, ensure_ascii=False)) <= max_chars
        return


async def _call_tool(server: AlephMCPServerLocal, tool_name: str, **kwargs: object) -> object:
    return await server.server._tool_manager.call_tool(  # type: ignore[attr-defined]
        tool_name,
        kwargs,
        convert_result=False,
    )


@pytest.fixture
async def big_context_server() -> tuple[AlephMCPServerLocal, str]:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=50_000),
        action_config=ActionConfig(context_policy="isolated"),
    )
    context = "SENSITIVE_MARKER_" + ("A" * 50_000) + "_END_MARKER"

    await _call_tool(server, "load_context", content=context, context_id="big")
    await _call_tool(server, "list_contexts")
    await _call_tool(server, "get_status", context_id="big")
    return server, context


def test_context_isolation_system_prompt_omits_sensitive_marker() -> None:
    assert "SENSITIVE_MARKER" not in DEFAULT_SYSTEM_PROMPT

    aleph = Aleph.__new__(Aleph)
    aleph.system_prompt = DEFAULT_SYSTEM_PROMPT + "\npreview={context_preview}"
    aleph.context_var_name = "ctx"
    meta = ContextMetadata(
        format=ContentFormat.TEXT,
        size_bytes=10,
        size_chars=10,
        size_lines=1,
        size_tokens_estimate=3,
        structure_hint="text",
        sample_preview="SENSITIVE_MARKER_TEST",
    )
    messages = aleph._build_initial_messages("question", meta)
    assert "SENSITIVE_MARKER" not in messages[0]["content"]


@pytest.mark.asyncio
async def test_get_variable_ctx_is_blocked(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(server, "get_variable", name="ctx", context_id="big")
    assert isinstance(result, str)
    assert "blocked" in result.lower() or "restricted" in result.lower()
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_exec_python_return_value_is_truncated(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(server, "exec_python", code="ctx", context_id="big")
    assert isinstance(result, str)
    assert "TRUNCATED" in result
    assert ("A" * 1000) not in result
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_exec_python_stdout_is_truncated(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(server, "exec_python", code="print(ctx)", context_id="big")
    assert isinstance(result, str)
    assert "TRUNCATED" in result
    assert ("A" * 1000) not in result
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_peek_context_is_bounded(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(
        server,
        "peek_context",
        start=0,
        end=99_999,
        unit="chars",
        context_id="big",
    )
    assert isinstance(result, str)
    assert "TRUNCATED" in result
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_search_context_is_bounded(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(
        server,
        "search_context",
        pattern="SENSITIVE_MARKER",
        context_id="big",
        max_results=50,
    )
    assert isinstance(result, str)
    assert "match" in result.lower()
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_get_variable_derived_value_safe_path(
    big_context_server: tuple[AlephMCPServerLocal, str],
) -> None:
    server, context = big_context_server
    await _call_tool(server, "exec_python", code="result = len(ctx)", context_id="big")
    result = await _call_tool(server, "get_variable", name="result", context_id="big")
    assert isinstance(result, int)
    assert result == len(context)


@pytest.mark.asyncio
async def test_semantic_search_is_bounded(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(server, "semantic_search", query="sensitive marker", context_id="big")
    assert isinstance(result, str)
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_exec_python_server_side_summary_stays_small(
    big_context_server: tuple[AlephMCPServerLocal, str],
) -> None:
    server, context = big_context_server
    await _call_tool(
        server,
        "exec_python",
        code=(
            "lines = ctx.splitlines()\n"
            'summary = f"Context has {len(lines)} lines, {len(ctx)} chars"'
        ),
        context_id="big",
    )
    summary = await _call_tool(server, "get_variable", name="summary", context_id="big")
    assert isinstance(summary, str)
    assert summary == f"Context has 1 lines, {len(context)} chars"
    _assert_response_bounded(summary)


@pytest.mark.asyncio
async def test_think_does_not_echo_raw_context(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(
        server,
        "think",
        question="What is the structure of the loaded context?",
        context_id="big",
    )
    assert isinstance(result, str)
    assert "Reasoning Step" in result
    assert "SENSITIVE_MARKER" not in result
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_get_variable_large_value_is_truncated(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    await _call_tool(server, "exec_python", code="big_var = 'X' * 100000", context_id="big")
    result = await _call_tool(server, "get_variable", name="big_var", context_id="big")
    assert isinstance(result, dict)
    assert result["truncated"] is True
    assert result["name"] == "big_var"
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_diff_contexts_is_bounded(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    await _call_tool(server, "load_context", content=("B" * 50_000), context_id="big2")
    result = await _call_tool(server, "diff_contexts", a="big", b="big2")
    assert isinstance(result, str)
    assert "TRUNCATED" in result
    assert ("A" * 1000) not in result
    assert ("B" * 1000) not in result
    _assert_response_bounded(result)


@pytest.mark.asyncio
async def test_save_session_response_does_not_return_ctx(tmp_path) -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=50_000),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )
    context = "SENSITIVE_MARKER_" + ("A" * 20_000) + "_END_MARKER"

    await _call_tool(server, "load_context", content=context, context_id="big")
    result = await _call_tool(
        server,
        "save_session",
        path="pack.json",
        confirm=True,
        output="object",
    )
    assert isinstance(result, dict)
    assert result["status"] == "success"
    assert "ctx" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_isolated_policy_disables_auto_memory_pack_and_allows_explicit_export(tmp_path) -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=50_000),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path, context_policy="isolated"),
    )
    await _call_tool(server, "load_context", content="secret context", context_id="iso")
    await _call_tool(server, "finalize", answer="done", context_id="iso")

    auto_pack = tmp_path / ".aleph" / "memory_pack.json"
    assert not auto_pack.exists()

    save_result = await _call_tool(
        server,
        "save_session",
        path="explicit-pack.json",
        confirm=True,
        output="object",
    )
    assert isinstance(save_result, dict)
    assert save_result["status"] == "success"
    explicit_payload = json.loads((tmp_path / "explicit-pack.json").read_text(encoding="utf-8"))
    assert explicit_payload["sessions"][0]["ctx"] == "secret context"


@pytest.mark.asyncio
async def test_isolated_policy_skips_auto_load_from_memory_pack(tmp_path) -> None:
    auto_dir = tmp_path / ".aleph"
    auto_dir.mkdir(parents=True, exist_ok=True)
    memory_pack = {
        "schema": "aleph.memory_pack.v1",
        "sessions": [
            {
                "schema": "aleph.session.v1",
                "session_id": "auto",
                "context_id": "auto",
                "created_at": "2026-01-01T00:00:00",
                "iterations": 0,
                "line_number_base": 1,
                "meta": {
                    "format": "text",
                    "size_bytes": 3,
                    "size_chars": 3,
                    "size_lines": 1,
                    "size_tokens_estimate": 1,
                    "structure_hint": None,
                    "sample_preview": "ctx",
                },
                "ctx": "ctx",
                "think_history": [],
                "confidence_history": [],
                "information_gain": [],
                "chunks": None,
                "tasks": [],
                "task_counter": 0,
                "evidence": [],
            }
        ],
    }
    (auto_dir / "memory_pack.json").write_text(json.dumps(memory_pack), encoding="utf-8")

    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=50_000),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path, context_policy="isolated"),
    )
    assert "auto" not in server._sessions


@pytest.mark.asyncio
async def test_isolated_get_variable_ctx_provides_alternatives(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    result = await _call_tool(server, "get_variable", name="ctx", context_id="big")
    assert isinstance(result, str)
    assert "blocked" in result.lower() or "restricted" in result.lower()
    assert "exec_python" in result
    assert "peek_context" in result
    assert "search_context" in result


@pytest.mark.asyncio
async def test_isolated_save_session_provides_guidance(big_context_server: tuple[AlephMCPServerLocal, str]) -> None:
    server, _ = big_context_server
    server.action_config.enabled = True
    result = await _call_tool(
        server, "save_session", path="test.json", confirm=False, output="markdown",
    )
    assert isinstance(result, str)
    assert "confirm=true" in result
    assert "configure" in result.lower()


@pytest.mark.asyncio
async def test_isolated_load_session_requires_confirm(tmp_path) -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=50_000),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path, context_policy="isolated"),
    )
    pack = {
        "schema": "aleph.memory_pack.v1",
        "sessions": [{
            "schema": "aleph.session.v1", "session_id": "s1", "context_id": "s1",
            "created_at": "2026-01-01T00:00:00", "iterations": 0, "line_number_base": 1,
            "meta": {"format": "text", "size_bytes": 3, "size_chars": 3,
                     "size_lines": 1, "size_tokens_estimate": 1},
            "ctx": "abc", "think_history": [], "confidence_history": [],
            "information_gain": [], "chunks": None, "tasks": [], "task_counter": 0,
            "evidence": [],
        }],
    }
    (tmp_path / "test_pack.json").write_text(json.dumps(pack), encoding="utf-8")

    # Without confirm: should be blocked with guidance
    result = await _call_tool(server, "load_session", path="test_pack.json", confirm=False, output="markdown")
    assert isinstance(result, str)
    assert "confirm=true" in result

    # With confirm: should succeed
    result = await _call_tool(server, "load_session", path="test_pack.json", confirm=True, output="object")
    assert isinstance(result, dict)
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_configure_policy_change_returns_guidance() -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=50_000),
    )
    result = await _call_tool(server, "configure", context_policy="isolated")
    assert isinstance(result, str)
    assert "isolated" in result
    assert "auto memory-pack" in result.lower() or "memory-pack" in result.lower()

    result = await _call_tool(server, "configure", context_policy="trusted")
    assert isinstance(result, str)
    assert "trusted" in result
    assert "restored" in result.lower()

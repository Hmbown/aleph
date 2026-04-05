"""Focused tests for the repl_injection extraction."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aleph.mcp.local_server import AlephMCPServerLocal, _Session, _analyze_text_context
from aleph.mcp.repl_injection import (
    configure_session,
    inject_repl_config_helpers,
    inject_repl_sub_aleph,
    inject_repl_sub_query,
)
from aleph.repl.sandbox import REPLEnvironment, SandboxConfig
from aleph.types import AlephResponse, ContentFormat


def _make_server() -> AlephMCPServerLocal:
    return AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=5000)
    )


def _make_session(text: str = "test context") -> _Session:
    meta = _analyze_text_context(text, ContentFormat.TEXT)
    repl = REPLEnvironment(
        context=text,
        context_var_name="ctx",
        config=SandboxConfig(timeout_seconds=5.0, max_output_chars=5000),
    )
    repl.set_variable("line_number_base", 1)
    return _Session(repl=repl, meta=meta, line_number_base=1)


class TestInjectReplConfigHelpers:
    def test_registers_set_backend_and_get_config(self) -> None:
        server = _make_server()
        session = _make_session()

        inject_repl_config_helpers(server, session)

        set_backend = session.repl.get_variable("set_backend")
        get_config = session.repl.get_variable("get_config")

        assert callable(set_backend)
        assert callable(get_config)
        with patch.dict("os.environ", {}, clear=False):
            result = set_backend("codex")
            snapshot = get_config()

        assert "sub_query_backend set to 'codex'" in result
        assert snapshot["sub_query_backend"] == "codex"


class TestInjectReplSubQuery:
    @pytest.mark.asyncio
    async def test_success_passthrough(self) -> None:
        server = _make_server()
        session = _make_session()
        server._run_sub_query = AsyncMock(return_value=(True, "OK", False, "codex"))  # type: ignore[method-assign]

        inject_repl_sub_query(server, session, "ctx1")

        result = await session.repl._sub_query_fn("summarize", "slice")  # type: ignore[misc]

        assert result == "OK"
        server._run_sub_query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_wraps_error(self) -> None:
        server = _make_server()
        session = _make_session()
        server._run_sub_query = AsyncMock(return_value=(False, "boom", False, "codex"))  # type: ignore[method-assign]

        inject_repl_sub_query(server, session, "ctx1")

        result = await session.repl._sub_query_fn("summarize", None)  # type: ignore[misc]

        assert result == "[ERROR: sub_query failed: boom]"


class TestInjectReplSubAleph:
    @pytest.mark.asyncio
    async def test_coerces_structured_context(self) -> None:
        server = _make_server()
        session = _make_session()
        response = AlephResponse(
            answer="done",
            success=True,
            total_iterations=1,
            max_depth_reached=1,
            total_tokens=0,
            total_cost_usd=0.0,
            wall_time_seconds=0.0,
            trajectory=[],
        )
        server._run_sub_aleph = AsyncMock(return_value=(response, {}))  # type: ignore[method-assign]

        inject_repl_sub_aleph(server, session, "ctx1")

        result = await session.repl._sub_aleph_fn("q", {"a": 1})  # type: ignore[misc]

        assert result is response
        kwargs = server._run_sub_aleph.await_args.kwargs
        assert kwargs["context_slice"] == '{\n  "a": 1\n}'
        assert kwargs["context_id"] == "ctx1"


class TestConfigureSession:
    @pytest.mark.asyncio
    async def test_sets_loop_and_injects_helpers(self) -> None:
        server = _make_server()
        session = _make_session()
        server._run_sub_query = AsyncMock(return_value=(True, "OK", False, "codex"))  # type: ignore[method-assign]
        response = AlephResponse(
            answer="done",
            success=True,
            total_iterations=1,
            max_depth_reached=1,
            total_tokens=0,
            total_cost_usd=0.0,
            wall_time_seconds=0.0,
            trajectory=[],
        )
        server._run_sub_aleph = AsyncMock(return_value=(response, {}))  # type: ignore[method-assign]

        loop = asyncio.get_running_loop()
        configure_session(server, session, "ctx1", loop=loop)

        assert session.repl._loop is loop  # type: ignore[attr-defined]
        assert callable(session.repl.get_variable("set_backend"))
        assert callable(session.repl.get_variable("get_config"))
        assert session.repl._sub_query_fn is not None  # type: ignore[attr-defined]
        assert session.repl._sub_aleph_fn is not None  # type: ignore[attr-defined]


class TestServerWrapperDelegation:
    @pytest.mark.asyncio
    async def test_server_configure_session_delegates(self) -> None:
        server = _make_server()
        session = _make_session()
        server._run_sub_query = AsyncMock(return_value=(True, "OK", False, "codex"))  # type: ignore[method-assign]
        response = AlephResponse(
            answer="done",
            success=True,
            total_iterations=1,
            max_depth_reached=1,
            total_tokens=0,
            total_cost_usd=0.0,
            wall_time_seconds=0.0,
            trajectory=[],
        )
        server._run_sub_aleph = AsyncMock(return_value=(response, {}))  # type: ignore[method-assign]

        server._configure_session(session, "ctx1", loop=asyncio.get_running_loop())

        assert callable(session.repl.get_variable("set_backend"))
        assert session.repl._sub_query_fn is not None  # type: ignore[attr-defined]
        assert session.repl._sub_aleph_fn is not None  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_server_inject_repl_sub_query_delegates(self) -> None:
        server = _make_server()
        session = _make_session()
        server._run_sub_query = AsyncMock(return_value=(True, "OK", False, "codex"))  # type: ignore[method-assign]

        server._inject_repl_sub_query(session, "ctx1")

        result = await session.repl._sub_query_fn("summarize", None)  # type: ignore[misc]
        assert result == "OK"

"""Focused tests for the node_bridge extraction.

Tests that:
1. close_node_repl / configure_node_repl / get_or_create_node_repl /
   sync_session_from_node_repl work as standalone functions
2. The server wrapper methods delegate correctly
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aleph.mcp.local_server import AlephMCPServerLocal, _Session, _analyze_text_context
from aleph.mcp.node_bridge import (
    close_node_repl,
    configure_node_repl,
    get_or_create_node_repl,
    sync_session_from_node_repl,
)
from aleph.repl.sandbox import REPLEnvironment, SandboxConfig
from aleph.types import ContentFormat


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


class TestCloseNodeRepl:
    def test_close_existing(self) -> None:
        mock_repl = MagicMock()
        node_repls: dict = {"ctx1": mock_repl}
        close_node_repl(node_repls, "ctx1")
        mock_repl.close.assert_called_once()
        assert "ctx1" not in node_repls

    def test_close_nonexistent(self) -> None:
        node_repls: dict = {}
        close_node_repl(node_repls, "missing")  # should not raise

    def test_close_leaves_others(self) -> None:
        mock1 = MagicMock()
        mock2 = MagicMock()
        node_repls: dict = {"a": mock1, "b": mock2}
        close_node_repl(node_repls, "a")
        assert "b" in node_repls
        mock2.close.assert_not_called()


class TestConfigureNodeRepl:
    def test_registers_expected_callbacks(self) -> None:
        session = _make_session()
        # Inject minimal callables so configure_node_repl can find them
        session.repl.set_variable("sub_query", lambda p, c=None: "ok")
        session.repl.set_variable("sub_aleph", lambda q, c=None: "ok")
        session.repl.set_variable("set_backend", lambda b: "ok")
        session.repl.set_variable("get_config", lambda: {})

        mock_repl = MagicMock()
        configure_node_repl(mock_repl, session)

        registered = {call.args[0] for call in mock_repl.register_callback.call_args_list}
        expected = {
            "sub_query", "sub_query_map", "sub_query_batch",
            "sub_query_strict", "sub_aleph", "set_backend", "get_config",
        }
        assert expected == registered


class TestGetOrCreateNodeRepl:
    def test_missing_session_raises(self) -> None:
        with pytest.raises(KeyError):
            get_or_create_node_repl({}, {}, "missing", SandboxConfig())

    @pytest.mark.asyncio
    async def test_creates_and_caches(self) -> None:
        sessions: dict = {"default": _make_session("hello world")}
        node_repls: dict = {}
        cfg = SandboxConfig(timeout_seconds=5.0)

        # Inject minimal callables for configure_node_repl
        sessions["default"].repl.set_variable("sub_query", lambda p, c=None: "ok")
        sessions["default"].repl.set_variable("sub_aleph", lambda q, c=None: "ok")
        sessions["default"].repl.set_variable("set_backend", lambda b: "ok")
        sessions["default"].repl.set_variable("get_config", lambda: {})

        repl = get_or_create_node_repl(node_repls, sessions, "default", cfg)
        assert "default" in node_repls
        assert node_repls["default"] is repl

        # Second call returns same instance (not recreated)
        repl2 = get_or_create_node_repl(node_repls, sessions, "default", cfg)
        assert repl2 is repl


class TestSyncSessionFromNodeRepl:
    def test_no_node_repl_returns_empty(self) -> None:
        result = sync_session_from_node_repl({}, {}, "missing", _analyze_text_context)
        assert result == []

    def test_no_session_returns_empty(self) -> None:
        mock_repl = MagicMock()
        result = sync_session_from_node_repl(
            {"ctx1": mock_repl}, {}, "ctx1", _analyze_text_context
        )
        assert result == []

    def test_syncs_context_back(self) -> None:
        session = _make_session("original text")
        sessions: dict = {"ctx1": session}

        mock_node_repl = MagicMock()
        mock_node_repl.get_variable.return_value = "updated text from node"
        mock_node_repl.drain_citations.return_value = [{"cite": "test"}]
        node_repls: dict = {"ctx1": mock_node_repl}

        citations = sync_session_from_node_repl(
            node_repls, sessions, "ctx1", _analyze_text_context
        )

        assert citations == [{"cite": "test"}]
        assert session.repl.get_variable("ctx") == "updated text from node"
        assert session.meta.size_chars == len("updated text from node")


class TestServerDelegation:
    """Verify server methods delegate to node_bridge functions."""

    def test_close_node_repl_delegates(self) -> None:
        server = _make_server()
        mock_repl = MagicMock()
        server._node_repls["test"] = mock_repl
        server._close_node_repl("test")
        mock_repl.close.assert_called_once()
        assert "test" not in server._node_repls

    def test_configure_node_repl_delegates(self) -> None:
        server = _make_server()
        session = _make_session()
        session.repl.set_variable("sub_query", lambda p, c=None: "ok")
        session.repl.set_variable("sub_aleph", lambda q, c=None: "ok")
        session.repl.set_variable("set_backend", lambda b: "ok")
        session.repl.set_variable("get_config", lambda: {})

        mock_repl = MagicMock()
        server._configure_node_repl(mock_repl, session)
        assert mock_repl.register_callback.call_count == 7

    def test_sync_session_from_node_repl_delegates(self) -> None:
        server = _make_server()
        session = _make_session("original")
        server._sessions["ctx1"] = session

        mock_node = MagicMock()
        mock_node.get_variable.return_value = "new text"
        mock_node.drain_citations.return_value = []
        server._node_repls["ctx1"] = mock_node

        result = server._sync_session_from_node_repl("ctx1")
        assert result == []
        assert session.repl.get_variable("ctx") == "new text"

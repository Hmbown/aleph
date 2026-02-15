"""Tests for LangGraph RLM-default integration helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aleph.integrations import langgraph_rlm


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.asyncio
async def test_build_aleph_mcp_tools_filters_required_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        last_servers: dict[str, Any] | None = None

        def __init__(self, servers: dict[str, Any]) -> None:
            _FakeClient.last_servers = servers

        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool("search_context"), _FakeTool("finalize"), _FakeTool("unused")]

    monkeypatch.setattr(
        langgraph_rlm,
        "_require_module",
        lambda _name, _hint: SimpleNamespace(MultiServerMCPClient=_FakeClient),
    )

    config = langgraph_rlm.AlephRLMConfig(
        transport="streamable_http",
        server_url="http://localhost:9876/mcp",
        required_tools=("search_context", "finalize"),
    )
    tools = await langgraph_rlm.build_aleph_mcp_tools(config)

    assert [tool.name for tool in tools] == ["search_context", "finalize"]
    assert _FakeClient.last_servers == {
        "aleph": {"transport": "http", "url": "http://localhost:9876/mcp"}
    }


class _FakeCompiledGraph:
    def __init__(self, checkpointer: Any) -> None:
        self.checkpointer = checkpointer
        self.calls: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    async def ainvoke(self, payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((payload, config))
        return payload


class _FakeStateGraph:
    instances: list["_FakeStateGraph"] = []

    def __init__(self, _schema: Any) -> None:
        self.nodes: dict[str, Any] = {}
        self.edges: list[tuple[str, str]] = []
        self.conditional_edges: list[tuple[str, dict[str, str]]] = []
        _FakeStateGraph.instances.append(self)

    def add_node(self, name: str, fn: Any) -> None:
        self.nodes[name] = fn

    def add_edge(self, start: str, end: str) -> None:
        self.edges.append((start, end))

    def add_conditional_edges(self, name: str, _router: Any, mapping: dict[str, str]) -> None:
        self.conditional_edges.append((name, mapping))

    def compile(self, checkpointer: Any | None = None) -> _FakeCompiledGraph:
        graph = _FakeCompiledGraph(checkpointer)
        setattr(graph, "_fake_workflow", self)
        return graph


@pytest.mark.asyncio
async def test_build_rlm_default_graph_compiles_explicit_topology_with_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = object()
    fake_tools = [_FakeTool("search_context")]

    async def _fake_create_client_and_tools(_config: langgraph_rlm.AlephRLMConfig) -> tuple[Any, list[Any]]:
        return fake_client, fake_tools

    monkeypatch.setattr(langgraph_rlm, "_create_client_and_tools", _fake_create_client_and_tools)
    monkeypatch.setattr(langgraph_rlm, "_resolve_chat_model", lambda _config: object())

    def _fake_require_module(name: str, _hint: str) -> Any:
        if name == "langgraph.graph":
            return SimpleNamespace(StateGraph=_FakeStateGraph, START="START", END="END")
        raise AssertionError(f"Unexpected module request: {name}")

    monkeypatch.setattr(langgraph_rlm, "_require_module", _fake_require_module)

    sentinel_checkpointer = object()
    config = langgraph_rlm.AlephRLMConfig(checkpointer=sentinel_checkpointer)
    graph = await langgraph_rlm.build_rlm_default_graph(config)

    assert getattr(graph, "_aleph_mcp_client") is fake_client
    assert getattr(graph, "_aleph_mcp_tools") == fake_tools
    assert getattr(graph, "_aleph_rlm_config") == config
    assert getattr(graph, "_aleph_rlm_topology") == [
        "plan",
        "call_model",
        "decide_recurse",
        "tool",
        "aggregate",
        "finalize",
    ]
    assert graph.checkpointer is sentinel_checkpointer

    workflow = _FakeStateGraph.instances[-1]
    assert set(workflow.nodes.keys()) == {
        "plan",
        "call_model",
        "decide_recurse",
        "tool",
        "aggregate",
        "finalize",
    }
    assert ("START", "plan") in workflow.edges
    assert ("plan", "call_model") in workflow.edges
    assert ("call_model", "decide_recurse") in workflow.edges
    assert ("tool", "aggregate") in workflow.edges
    assert ("aggregate", "call_model") in workflow.edges
    assert ("finalize", "END") in workflow.edges
    assert workflow.conditional_edges == [
        ("decide_recurse", {"tool": "tool", "finalize": "finalize"})
    ]


class _RetryGraph:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def ainvoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((payload, config))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


@pytest.mark.asyncio
async def test_invoke_rlm_retries_for_data_heavy_queries() -> None:
    graph = _RetryGraph(
        responses=[
            {
                "messages": [{"role": "assistant", "content": "Answer without tools"}],
                "subcalls": [],
            },
            {
                "messages": [
                    {"role": "tool", "name": "search_context", "content": "found"},
                    {"role": "assistant", "content": "Tool-backed answer"},
                ],
                "subcalls": ["search_context"],
            },
        ]
    )
    config = langgraph_rlm.AlephRLMConfig(tool_retry_attempts=1, timeout_seconds=1.0)

    result = await langgraph_rlm.invoke_rlm(
        graph,
        "Analyze this JSON log and summarize failure clusters",
        thread_id="thread-1",
        config=config,
    )

    assert len(graph.calls) == 2
    assert graph.calls[0][1]["configurable"]["thread_id"] == "thread-1"
    assert graph.calls[0][0]["recursion_depth"] == 0
    assert result["subcalls"] == ["search_context"]


@pytest.mark.asyncio
async def test_invoke_rlm_skips_retry_for_non_data_heavy_queries() -> None:
    graph = _RetryGraph(
        responses=[{"messages": [{"role": "assistant", "content": "hello"}], "subcalls": []}]
    )
    config = langgraph_rlm.AlephRLMConfig(tool_retry_attempts=2, timeout_seconds=1.0)

    await langgraph_rlm.invoke_rlm(graph, "hello there", config=config)

    assert len(graph.calls) == 1


def test_collect_tool_trace_reads_subcalls_messages_and_tool_calls() -> None:
    result = {
        "subcalls": ["search_context"],
        "messages": [
            {"role": "tool", "name": "search_context", "content": "..."},
            {
                "role": "assistant",
                "content": "...",
                "tool_calls": [{"name": "peek_context"}, {"name": "exec_python"}],
            },
        ],
    }

    names = langgraph_rlm.collect_tool_trace(result)
    assert names == ["search_context", "search_context", "peek_context", "exec_python"]


def test_build_server_config_for_stdio_and_http() -> None:
    stdio_config = langgraph_rlm.AlephRLMConfig(transport="stdio", command="aleph", args=("--foo", "bar"))
    assert langgraph_rlm._build_server_config(stdio_config) == {
        "transport": "stdio",
        "command": "aleph",
        "args": ["--foo", "bar"],
    }

    http_config = langgraph_rlm.AlephRLMConfig(transport="streamable_http", server_url="http://x/y")
    assert langgraph_rlm._build_server_config(http_config) == {
        "transport": "http",
        "url": "http://x/y",
    }


def test_decide_next_action_respects_max_depth() -> None:
    assert (
        langgraph_rlm._decide_next_action(
            has_tool_calls=True,
            recursion_depth=0,
            max_recursion_depth=2,
        )
        == "tool"
    )
    assert (
        langgraph_rlm._decide_next_action(
            has_tool_calls=True,
            recursion_depth=2,
            max_recursion_depth=2,
        )
        == "finalize"
    )
    assert (
        langgraph_rlm._decide_next_action(
            has_tool_calls=False,
            recursion_depth=0,
            max_recursion_depth=2,
        )
        == "finalize"
    )

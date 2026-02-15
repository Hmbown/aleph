"""LangGraph integration that defaults to Aleph RLM-style tool use.

This module builds an explicit recursive state graph:
plan -> call_model -> decide_recurse -> tool -> aggregate -> call_model ... -> finalize

LangChain/LangGraph imports are optional and loaded lazily.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypedDict, cast

DEFAULT_REQUIRED_TOOLS: tuple[str, ...] = (
    "load_context",
    "search_context",
    "semantic_search",
    "peek_context",
    "exec_python",
    "sub_query",
    "sub_aleph",
    "finalize",
)

DEFAULT_SYSTEM_POLICY = """You are an RLM-first assistant backed by Aleph tools.

Behavior requirements:
1. When a question depends on local/project/document context, call Aleph tools before answering.
2. Prefer this sequence for large contexts: load_context/load_file -> search_context or semantic_search -> peek_context/exec_python -> finalize.
3. Use sub_query/sub_aleph for decomposition when direct inspection is insufficient.
4. Cite evidence from tool outputs in your final response.
5. If tools fail, explain the failure and provide the best possible fallback answer.
"""

_TOOL_RETRY_INSTRUCTION = (
    "This request appears context-dependent. Before answering, call at least one Aleph "
    "tool (for example search_context, semantic_search, peek_context, or exec_python)."
)

_DATA_HEAVY_HINTS: tuple[str, ...] = (
    "analy",
    "search",
    "find",
    "summar",
    "trace",
    "log",
    "dataset",
    "context",
    "document",
    "file",
    "repo",
    "codebase",
    "csv",
    "json",
    "error",
)


class AlephRLMState(TypedDict, total=False):
    """Graph state for the recursive RLM workflow."""

    messages: list[Any]
    recursion_depth: int
    plan: str
    subcalls: list[str]
    intermediate_summaries: list[str]
    next_action: Literal["tool", "finalize"]
    final_answer: str


@dataclass(slots=True)
class AlephRLMConfig:
    """Configuration for an Aleph-backed LangGraph recursive graph."""

    transport: Literal["stdio", "streamable_http", "http"] = "stdio"
    server_url: str = "http://127.0.0.1:8765/mcp"
    command: str = "aleph"
    args: tuple[str, ...] = ()
    mcp_server_name: str = "aleph"

    model: str = "openai:gpt-4.1-mini"
    chat_model: Any | None = None
    chat_model_factory: Callable[[str], Any] | None = None

    system_policy: str = DEFAULT_SYSTEM_POLICY
    required_tools: tuple[str, ...] = DEFAULT_REQUIRED_TOOLS

    max_steps: int = 24
    max_recursion_depth: int = 4
    max_summary_chars: int = 500
    timeout_seconds: float = 120.0
    tool_retry_attempts: int = 1

    enable_checkpointing: bool = True
    checkpointer: Any | None = None


def _require_module(module_name: str, install_hint: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - error path
        raise RuntimeError(
            f"Missing optional dependency '{module_name}'. Install with: {install_hint}"
        ) from exc


def _build_server_config(config: AlephRLMConfig) -> dict[str, Any]:
    if config.transport == "stdio":
        return {
            "transport": "stdio",
            "command": config.command,
            "args": list(config.args),
        }
    if config.transport in {"streamable_http", "http"}:
        return {
            "transport": "http",
            "url": config.server_url,
        }
    raise ValueError(f"Unsupported transport '{config.transport}'.")


async def _create_client_and_tools(config: AlephRLMConfig) -> tuple[Any, list[Any]]:
    module = _require_module(
        "langchain_mcp_adapters.client",
        "pip install langchain-mcp-adapters",
    )
    client_cls = getattr(module, "MultiServerMCPClient", None)
    if client_cls is None:
        raise RuntimeError(
            "langchain-mcp-adapters is installed but MultiServerMCPClient was not found. "
            "Upgrade to a recent version of langchain-mcp-adapters."
        )

    client = client_cls({config.mcp_server_name: _build_server_config(config)})
    tools = await client.get_tools()

    if not config.required_tools:
        return client, list(tools)

    required = set(config.required_tools)
    filtered = [tool for tool in tools if getattr(tool, "name", None) in required]
    return client, filtered


async def build_aleph_mcp_tools(config: AlephRLMConfig) -> list[Any]:
    """Build Aleph MCP tools for LangChain/LangGraph usage."""

    _, tools = await _create_client_and_tools(config)
    return tools


def _resolve_chat_model(config: AlephRLMConfig) -> Any:
    if config.chat_model is not None:
        return config.chat_model
    if config.chat_model_factory is not None:
        return config.chat_model_factory(config.model)

    chat_models_module = _require_module(
        "langchain.chat_models",
        "pip install langchain",
    )
    init_chat_model = getattr(chat_models_module, "init_chat_model", None)
    if init_chat_model is None:
        raise RuntimeError(
            "langchain.chat_models.init_chat_model is unavailable. "
            "Provide AlephRLMConfig.chat_model or AlephRLMConfig.chat_model_factory."
        )

    try:
        return init_chat_model(config.model)
    except TypeError:
        return init_chat_model(model=config.model)


def _resolve_default_checkpointer(config: AlephRLMConfig) -> Any | None:
    if not config.enable_checkpointing:
        return None
    if config.checkpointer is not None:
        return config.checkpointer

    try:
        checkpoint_module = importlib.import_module("langgraph.checkpoint.memory")
        memory_saver = getattr(checkpoint_module, "MemorySaver", None)
        if callable(memory_saver):
            return memory_saver()
    except Exception:
        return None
    return None


def _bind_tools_if_supported(model: Any, tools: list[Any]) -> Any:
    bind_tools = getattr(model, "bind_tools", None)
    if callable(bind_tools):
        try:
            return bind_tools(tools)
        except Exception:
            return model
    return model


async def _invoke_runnable(runnable: Any, payload: Any, timeout_seconds: float) -> Any:
    ainvoke = getattr(runnable, "ainvoke", None)
    if callable(ainvoke):
        return await asyncio.wait_for(ainvoke(payload), timeout=timeout_seconds)

    invoke = getattr(runnable, "invoke", None)
    if callable(invoke):
        return await asyncio.wait_for(asyncio.to_thread(invoke, payload), timeout=timeout_seconds)

    if callable(runnable):
        result = runnable(payload)
        if inspect.isawaitable(result):
            return await asyncio.wait_for(cast(Any, result), timeout=timeout_seconds)
        return result

    raise RuntimeError("Runnable does not expose ainvoke/invoke/callable interface.")


def _trim_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _message_field(message: Any, field: str) -> Any:
    if isinstance(message, dict):
        return message.get(field)
    return getattr(message, field, None)


def _message_content_text(message: Any) -> str:
    content = _message_field(message, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
            elif isinstance(item, str):
                chunks.append(item)
        return "\n".join(chunks)
    if content is None:
        return ""
    return str(content)


def _coerce_assistant_message(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    role = _message_field(value, "role")
    if role is not None:
        return value
    return {
        "role": "assistant",
        "content": _message_content_text(value) if value is not None else "",
    }


def _tool_calls_from_message(message: Any) -> list[dict[str, Any]]:
    tool_calls = _message_field(message, "tool_calls")
    if isinstance(tool_calls, list):
        normalized: list[dict[str, Any]] = []
        for item in tool_calls:
            if isinstance(item, dict):
                normalized.append(item)
            else:
                name = _message_field(item, "name")
                args = _message_field(item, "args")
                call_id = _message_field(item, "id")
                normalized.append({"name": name, "args": args, "id": call_id})
        return normalized
    return []


def _coerce_tool_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {"input": value}
    if value is None:
        return {}
    return {"input": str(value)}


def _latest_user_query(messages: list[Any]) -> str:
    for message in reversed(messages):
        role = _message_field(message, "role")
        msg_type = _message_field(message, "type")
        if role == "user" or msg_type == "human":
            text = _message_content_text(message).strip()
            if text:
                return text
    return ""


def _state_with_defaults(state: AlephRLMState | dict[str, Any]) -> AlephRLMState:
    return {
        "messages": list(state.get("messages", [])),
        "recursion_depth": int(state.get("recursion_depth", 0)),
        "plan": str(state.get("plan", "")),
        "subcalls": list(state.get("subcalls", [])),
        "intermediate_summaries": list(state.get("intermediate_summaries", [])),
        "final_answer": str(state.get("final_answer", "")),
    }


def _initial_state(user_input: str, extra_instruction: str | None = None) -> AlephRLMState:
    messages: list[Any] = [{"role": "user", "content": user_input}]
    if extra_instruction:
        messages.append({"role": "user", "content": extra_instruction})
    return {
        "messages": messages,
        "recursion_depth": 0,
        "plan": "",
        "subcalls": [],
        "intermediate_summaries": [],
        "final_answer": "",
    }


def _recent_tool_messages(messages: list[Any]) -> list[Any]:
    collected: list[Any] = []
    for message in reversed(messages):
        role = _message_field(message, "role")
        msg_type = _message_field(message, "type")
        if role == "tool" or msg_type == "tool":
            collected.append(message)
            continue
        break
    return list(reversed(collected))


def _result_has_tool_activity(result: Any) -> bool:
    if isinstance(result, dict):
        subcalls = result.get("subcalls")
        if isinstance(subcalls, list) and subcalls:
            return True
    for message in _iter_messages(result):
        msg_type = _message_field(message, "type")
        role = _message_field(message, "role")
        if msg_type == "tool" or role == "tool":
            return True
        tool_calls = _message_field(message, "tool_calls")
        if tool_calls:
            return True
    return False


def _decide_next_action(
    *,
    has_tool_calls: bool,
    recursion_depth: int,
    max_recursion_depth: int,
) -> Literal["tool", "finalize"]:
    if has_tool_calls and recursion_depth < max_recursion_depth:
        return "tool"
    return "finalize"


async def _invoke_tool(tool: Any, args: dict[str, Any], timeout_seconds: float) -> str:
    try:
        ainvoke = getattr(tool, "ainvoke", None)
        if callable(ainvoke):
            output = await asyncio.wait_for(ainvoke(args), timeout=timeout_seconds)
            return _message_content_text(output) if not isinstance(output, str) else output

        invoke = getattr(tool, "invoke", None)
        if callable(invoke):
            output = await asyncio.wait_for(asyncio.to_thread(invoke, args), timeout=timeout_seconds)
            return _message_content_text(output) if not isinstance(output, str) else output

        if callable(tool):
            result = tool(**args) if args else tool()
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(cast(Any, result), timeout=timeout_seconds)
            return _message_content_text(result) if not isinstance(result, str) else result

        return "[TOOL ERROR: tool is not callable]"
    except Exception as exc:
        return f"[TOOL ERROR: {exc}]"


def _iter_messages(result: Any) -> list[Any]:
    if isinstance(result, dict):
        value = result.get("messages")
        if isinstance(value, list):
            return value
    return []


def _looks_data_heavy(query: str) -> bool:
    lowered = query.lower()
    return any(hint in lowered for hint in _DATA_HEAVY_HINTS)


def _route_after_decide(state: AlephRLMState | dict[str, Any]) -> str:
    return cast(str, state.get("next_action", "finalize"))


async def build_rlm_default_graph(config: AlephRLMConfig) -> Any:
    """Construct an explicit recursive LangGraph workflow backed by Aleph tools."""

    client, tools = await _create_client_and_tools(config)
    base_model = _resolve_chat_model(config)
    tool_model = _bind_tools_if_supported(base_model, tools)
    tools_by_name = {
        cast(str, getattr(tool, "name", f"tool_{idx}")): tool for idx, tool in enumerate(tools)
    }

    graph_module = _require_module("langgraph.graph", "pip install langgraph")
    state_graph_cls = getattr(graph_module, "StateGraph", None)
    start = getattr(graph_module, "START", None)
    end = getattr(graph_module, "END", None)
    if state_graph_cls is None or start is None or end is None:
        raise RuntimeError(
            "langgraph.graph is present but missing StateGraph/START/END. Upgrade langgraph."
        )

    async def _plan_node(state: AlephRLMState | dict[str, Any]) -> AlephRLMState:
        snapshot = _state_with_defaults(state)
        if snapshot["plan"].strip():
            return snapshot

        query = _latest_user_query(snapshot["messages"])
        if not query:
            snapshot["plan"] = "Use Aleph tools to inspect context, then synthesize a concise answer."
            return snapshot

        prompt = (
            "Create a short execution plan for this query using Aleph tools. "
            "Keep it to 3-5 concise steps.\n\n"
            f"Query:\n{query}"
        )
        try:
            response = await _invoke_runnable(
                base_model,
                [
                    {"role": "system", "content": config.system_policy},
                    {"role": "user", "content": prompt},
                ],
                timeout_seconds=config.timeout_seconds,
            )
            text = _message_content_text(response).strip()
            snapshot["plan"] = text or "Use Aleph tools to inspect context, then synthesize a concise answer."
        except Exception:
            snapshot["plan"] = "Use Aleph tools to inspect context, then synthesize a concise answer."
        return snapshot

    async def _call_model_node(state: AlephRLMState | dict[str, Any]) -> AlephRLMState:
        snapshot = _state_with_defaults(state)
        plan = snapshot["plan"] or "Use Aleph tools to gather evidence before answering."
        query = _latest_user_query(snapshot["messages"])

        model_messages: list[Any] = [
            {"role": "system", "content": config.system_policy},
            {
                "role": "system",
                "content": (
                    "Current plan:\n"
                    f"{plan}\n\n"
                    f"Current recursion depth: {snapshot['recursion_depth']} / {config.max_recursion_depth}."
                ),
            },
            *snapshot["messages"],
        ]
        if query and snapshot["recursion_depth"] >= config.max_recursion_depth:
            model_messages.append(
                {
                    "role": "system",
                    "content": "Max recursion depth reached. Synthesize the best final answer from gathered evidence.",
                }
            )

        response = await _invoke_runnable(
            tool_model,
            model_messages,
            timeout_seconds=config.timeout_seconds,
        )
        snapshot["messages"].append(_coerce_assistant_message(response))
        return snapshot

    async def _decide_node(state: AlephRLMState | dict[str, Any]) -> AlephRLMState:
        snapshot = _state_with_defaults(state)
        last_message = snapshot["messages"][-1] if snapshot["messages"] else None
        tool_calls = _tool_calls_from_message(last_message)

        action = _decide_next_action(
            has_tool_calls=bool(tool_calls),
            recursion_depth=snapshot["recursion_depth"],
            max_recursion_depth=config.max_recursion_depth,
        )
        snapshot["next_action"] = action
        if action == "tool" and tool_calls:
            snapshot["recursion_depth"] += 1
        elif tool_calls and action == "finalize":
            snapshot["intermediate_summaries"].append(
                "Reached recursion depth limit; finalizing with collected evidence."
            )
        return snapshot

    async def _tool_node(state: AlephRLMState | dict[str, Any]) -> AlephRLMState:
        snapshot = _state_with_defaults(state)
        last_message = snapshot["messages"][-1] if snapshot["messages"] else None
        tool_calls = _tool_calls_from_message(last_message)
        if not tool_calls:
            return snapshot

        tool_messages: list[dict[str, Any]] = []
        for idx, call in enumerate(tool_calls):
            name_val = call.get("name")
            name = str(name_val) if isinstance(name_val, str) else f"unknown_tool_{idx}"
            args_payload = call.get("args", call.get("arguments"))
            args = _coerce_tool_args(args_payload)

            snapshot["subcalls"].append(name)
            tool = tools_by_name.get(name)
            if tool is None:
                output = f"[TOOL ERROR: unknown tool '{name}']"
            else:
                output = await _invoke_tool(tool, args, timeout_seconds=config.timeout_seconds)

            tool_messages.append(
                {
                    "role": "tool",
                    "name": name,
                    "content": output,
                    "tool_call_id": call.get("id"),
                }
            )

        snapshot["messages"].extend(tool_messages)
        return snapshot

    async def _aggregate_node(state: AlephRLMState | dict[str, Any]) -> AlephRLMState:
        snapshot = _state_with_defaults(state)
        recent_tools = _recent_tool_messages(snapshot["messages"])
        if not recent_tools:
            return snapshot

        for message in recent_tools:
            name = _message_field(message, "name")
            label = str(name) if isinstance(name, str) else "tool"
            content = _trim_text(_message_content_text(message), config.max_summary_chars)
            summary = f"{label}: {content}" if content else f"{label}: <empty output>"
            snapshot["intermediate_summaries"].append(summary)
        return snapshot

    async def _finalize_node(state: AlephRLMState | dict[str, Any]) -> AlephRLMState:
        snapshot = _state_with_defaults(state)
        messages = snapshot["messages"]
        if messages:
            last = messages[-1]
            if not _tool_calls_from_message(last):
                text = _message_content_text(last).strip()
                if text:
                    snapshot["final_answer"] = text
                    return snapshot

        query = _latest_user_query(messages)
        plan = snapshot["plan"]
        summaries = "\n".join(snapshot["intermediate_summaries"][-10:]).strip()

        synthesis_prompt = (
            "Produce the final answer from the available evidence.\n\n"
            f"Query:\n{query}\n\n"
            f"Plan:\n{plan}\n\n"
            f"Evidence summaries:\n{summaries if summaries else '(none)'}"
        )
        fallback = summaries or "No tool evidence captured."

        try:
            response = await _invoke_runnable(
                base_model,
                [
                    {"role": "system", "content": "Synthesize a final answer grounded in tool evidence."},
                    {"role": "user", "content": synthesis_prompt},
                ],
                timeout_seconds=config.timeout_seconds,
            )
            text = _message_content_text(response).strip()
            if text:
                snapshot["messages"].append(_coerce_assistant_message(response))
                snapshot["final_answer"] = text
                return snapshot
        except Exception:
            pass

        snapshot["final_answer"] = fallback
        return snapshot

    workflow = state_graph_cls(dict)
    workflow.add_node("plan", _plan_node)
    workflow.add_node("call_model", _call_model_node)
    workflow.add_node("decide_recurse", _decide_node)
    workflow.add_node("tool", _tool_node)
    workflow.add_node("aggregate", _aggregate_node)
    workflow.add_node("finalize", _finalize_node)

    workflow.add_edge(start, "plan")
    workflow.add_edge("plan", "call_model")
    workflow.add_edge("call_model", "decide_recurse")
    workflow.add_conditional_edges(
        "decide_recurse",
        _route_after_decide,
        {
            "tool": "tool",
            "finalize": "finalize",
        },
    )
    workflow.add_edge("tool", "aggregate")
    workflow.add_edge("aggregate", "call_model")
    workflow.add_edge("finalize", end)

    checkpointer = _resolve_default_checkpointer(config)
    if checkpointer is None:
        graph = workflow.compile()
    else:
        graph = workflow.compile(checkpointer=checkpointer)

    setattr(graph, "_aleph_mcp_client", client)
    setattr(graph, "_aleph_mcp_tools", tools)
    setattr(graph, "_aleph_rlm_config", config)
    setattr(
        graph,
        "_aleph_rlm_topology",
        ["plan", "call_model", "decide_recurse", "tool", "aggregate", "finalize"],
    )
    return graph


async def _invoke_graph_once(
    graph: Any,
    payload: dict[str, Any],
    run_config: dict[str, Any],
    timeout_seconds: float,
) -> Any:
    if hasattr(graph, "ainvoke") and callable(graph.ainvoke):
        try:
            awaitable = graph.ainvoke(payload, run_config)
        except TypeError:
            awaitable = graph.ainvoke(payload)
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)

    if hasattr(graph, "invoke") and callable(graph.invoke):

        def _run_sync() -> Any:
            try:
                return graph.invoke(payload, run_config)
            except TypeError:
                return graph.invoke(payload)

        return await asyncio.wait_for(asyncio.to_thread(_run_sync), timeout=timeout_seconds)

    raise RuntimeError("Graph object does not expose invoke/ainvoke.")


async def invoke_rlm(
    graph: Any,
    user_input: str,
    *,
    thread_id: str | None = None,
    config: AlephRLMConfig | None = None,
) -> Any:
    """Invoke a LangGraph RLM workflow with retry and checkpoint-friendly config."""

    effective_config = config or cast(
        AlephRLMConfig | None,
        getattr(graph, "_aleph_rlm_config", None),
    )
    if effective_config is None:
        effective_config = AlephRLMConfig()

    run_config: dict[str, Any] = {}
    if effective_config.max_steps > 0:
        run_config["recursion_limit"] = effective_config.max_steps
    if thread_id:
        run_config["configurable"] = {"thread_id": thread_id}

    result = await _invoke_graph_once(
        graph,
        _initial_state(user_input),
        run_config,
        timeout_seconds=effective_config.timeout_seconds,
    )

    if (
        effective_config.tool_retry_attempts > 0
        and _looks_data_heavy(user_input)
        and not _result_has_tool_activity(result)
    ):
        for _ in range(effective_config.tool_retry_attempts):
            result = await _invoke_graph_once(
                graph,
                _initial_state(user_input, extra_instruction=_TOOL_RETRY_INSTRUCTION),
                run_config,
                timeout_seconds=effective_config.timeout_seconds,
            )
            if _result_has_tool_activity(result):
                break

    return result


def collect_tool_trace(result: Any) -> list[str]:
    """Collect tool names from graph output messages/subcalls for tracing."""

    names: list[str] = []

    if isinstance(result, dict):
        subcalls = result.get("subcalls")
        if isinstance(subcalls, list):
            for name in subcalls:
                if isinstance(name, str) and name:
                    names.append(name)

    for message in _iter_messages(result):
        if _message_field(message, "type") == "tool" or _message_field(message, "role") == "tool":
            name = _message_field(message, "name")
            if isinstance(name, str) and name:
                names.append(name)

        tool_calls = _message_field(message, "tool_calls")
        if isinstance(tool_calls, list):
            for item in tool_calls:
                if isinstance(item, dict):
                    name = item.get("name")
                    if isinstance(name, str) and name:
                        names.append(name)
    return names


__all__ = [
    "AlephRLMConfig",
    "AlephRLMState",
    "build_aleph_mcp_tools",
    "build_rlm_default_graph",
    "invoke_rlm",
    "collect_tool_trace",
]

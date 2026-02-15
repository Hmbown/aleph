"""LangGraph example: use Aleph MCP tools as the default reasoning path.

Environment variables:
- ALEPH_MCP_TRANSPORT: `stdio` (default) or `streamable_http`
- ALEPH_MCP_URL: HTTP URL when using streamable_http
- ALEPH_MCP_COMMAND: command for stdio mode (default: `aleph`)
- ALEPH_MCP_ARGS: space-separated args for stdio mode
- MODEL: LangChain model spec (default: openai:gpt-4.1-mini)

Run:
    python examples/langgraph_rlm_default.py --query "Analyze this context for recurring errors"
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, Literal, cast

from aleph.integrations.langgraph_rlm import (
    AlephRLMConfig,
    build_rlm_default_graph,
    collect_tool_trace,
    invoke_rlm,
)

_DEFAULT_CONTEXT = """2026-02-10 10:00:11 ERROR Auth timeout for user=u123
2026-02-10 10:00:15 INFO Retry succeeded for user=u123
2026-02-10 10:01:42 ERROR Auth timeout for user=u918
2026-02-10 10:02:02 WARN Elevated latency observed in auth service
2026-02-10 10:03:23 ERROR Token verification failed for user=u404
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an Aleph-backed RLM-default LangGraph example")
    parser.add_argument(
        "--query",
        default="Find recurring auth failures and summarize likely causes.",
        help="User query for the graph",
    )
    parser.add_argument(
        "--context",
        default=_DEFAULT_CONTEXT,
        help="Text to preload into Aleph context",
    )
    parser.add_argument(
        "--context-id",
        default="demo_logs",
        help="Aleph context id used during preload",
    )
    parser.add_argument(
        "--thread-id",
        default="langgraph-rlm-demo",
        help="Thread id for LangGraph checkpointing",
    )
    return parser.parse_args()


def _env_config() -> AlephRLMConfig:
    transport_env = os.environ.get("ALEPH_MCP_TRANSPORT", "stdio").strip().lower()
    if transport_env not in {"stdio", "streamable_http", "http"}:
        transport_env = "stdio"
    args_env = os.environ.get("ALEPH_MCP_ARGS", "").strip()
    args = tuple(args_env.split()) if args_env else ()

    transport = cast(Literal["stdio", "streamable_http", "http"], transport_env)

    return AlephRLMConfig(
        transport=transport,
        server_url=os.environ.get("ALEPH_MCP_URL", "http://127.0.0.1:8765/mcp"),
        command=os.environ.get("ALEPH_MCP_COMMAND", "aleph"),
        args=args,
        model=os.environ.get("MODEL", "openai:gpt-4.1-mini"),
    )


def _message_field(message: Any, field: str) -> Any:
    if isinstance(message, dict):
        return message.get(field)
    return getattr(message, field, None)


def _extract_last_answer(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)
    final_answer = result.get("final_answer")
    if isinstance(final_answer, str) and final_answer.strip():
        return final_answer
    messages = result.get("messages", [])
    if not isinstance(messages, list):
        return str(result)

    for message in reversed(messages):
        role = _message_field(message, "role")
        msg_type = _message_field(message, "type")
        if role in {"assistant", "ai"} or msg_type in {"assistant", "ai"}:
            content = _message_field(message, "content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                chunks: list[str] = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        chunks.append(item["text"])
                if chunks:
                    return "\n".join(chunks)
    return str(result)


async def _run() -> None:
    args = _parse_args()
    config = _env_config()

    graph = await build_rlm_default_graph(config)

    preload_prompt = (
        f"Call load_context with context_id='{args.context_id}' and this exact text:\n"
        f"```text\n{args.context}\n```"
    )
    await invoke_rlm(graph, preload_prompt, thread_id=args.thread_id, config=config)

    result = await invoke_rlm(graph, args.query, thread_id=args.thread_id, config=config)
    tools = collect_tool_trace(result)

    print("\n=== Tool Trace ===")
    if tools:
        for name in tools:
            print(f"- {name}")
    else:
        print("(no tool activity captured)")

    print("\n=== Final Answer ===")
    print(_extract_last_answer(result))


if __name__ == "__main__":
    asyncio.run(_run())

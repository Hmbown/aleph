# LangGraph RLM-Default Integration

This guide shows how to run LangGraph with Aleph MCP tools as the default path for context-heavy reasoning.

## What "RLM default" means

In this integration, the graph is configured so that context-dependent questions should use Aleph tools first:

1. `load_context` or `load_file`
2. `search_context` or `semantic_search`
3. `peek_context` or `exec_python`
4. `sub_query` or `sub_aleph` when decomposition is needed
5. `finalize`

If the first graph pass does not trigger tool activity for a data-heavy prompt, the helper retries with a stricter instruction.

## Graph topology (implemented)

The integration builds an explicit recursive LangGraph workflow:

1. `plan`
2. `call_model`
3. `decide_recurse`
4. `tool` (when tool calls exist and depth budget allows)
5. `aggregate`
6. loop back to `call_model`
7. `finalize`

Graph state includes:

- `messages`
- `recursion_depth`
- `plan`
- `subcalls`
- `intermediate_summaries`
- `final_answer`

`decide_recurse` enforces a recursion depth cap via `AlephRLMConfig.max_recursion_depth`.

## Install

```bash
pip install "aleph-rlm[mcp]"
pip install langchain langgraph langchain-mcp-adapters langchain-openai langsmith
```

## Start Aleph MCP server

Stdio transport (default) works directly with MCP adapters:

```bash
aleph
```

If you already expose Aleph over streamable HTTP in your environment, use that URL (for example `http://127.0.0.1:8765/mcp`).

## Quickstart (Python)

```python
import asyncio
from aleph.integrations.langgraph_rlm import (
    AlephRLMConfig,
    build_rlm_default_graph,
    invoke_rlm,
)


async def main() -> None:
    cfg = AlephRLMConfig(
        transport="stdio",
        command="aleph",
        model="openai:gpt-4.1-mini",
    )

    graph = await build_rlm_default_graph(cfg)

    await invoke_rlm(
        graph,
        "Load this content with context_id='doc':\\n```text\\n...big text...\\n```",
        thread_id="demo",
        config=cfg,
    )

    result = await invoke_rlm(
        graph,
        "Analyze recurring errors and cite evidence.",
        thread_id="demo",
        config=cfg,
    )
    print(result)


asyncio.run(main())
```

## Explicit stdio config

Use this when you want to set stdio settings explicitly:

```python
cfg = AlephRLMConfig(
    transport="stdio",
    command="aleph",
    model="openai:gpt-4.1-mini",
)
```

## API Reference

- `AlephRLMConfig`: integration config
- `build_aleph_mcp_tools(config)`: create Aleph-backed LangChain tools
- `build_rlm_default_graph(config)`: build graph/agent wired to Aleph tools
- `invoke_rlm(graph, user_input, thread_id=None, config=None)`: invoke with retry policy
- `collect_tool_trace(result)`: extract tool activity names from graph output

## Checkpointing and resumability

- `build_rlm_default_graph` compiles with checkpointing when available.
- If `AlephRLMConfig.checkpointer` is provided, it is used directly.
- If not provided and `enable_checkpointing=True`, the integration attempts to use LangGraph `MemorySaver`.
- Pass `thread_id` to `invoke_rlm(...)` to resume the same thread state across calls and make runs debuggable.

## Example script

Run the included example:

```bash
python examples/langgraph_rlm_default.py --query "Find recurring auth failures"
```

It preloads sample context, runs a query, prints tool trace, and prints the final answer.

### Repo improver runner (LangSmith-ready)

Use the repo-focused runner to load selected files and ask for concrete improvement proposals:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=<your_key>
export LANGSMITH_PROJECT=aleph-rlm
export OPENAI_API_KEY=<your_model_key>

python examples/langgraph_rlm_repo_improver.py \
  --files README.md pyproject.toml aleph/integrations/langgraph_rlm.py \
  --thread-id repo-improver-1
```

Dry-run setup validation (no model call):

```bash
python examples/langgraph_rlm_repo_improver.py --dry-run
```

## Failure behavior

- Missing integration dependencies raise actionable install errors.
- Tool failures are surfaced through graph output messages from the underlying agent/tool stack.
- If no tool activity is detected for context-heavy prompts, `invoke_rlm` performs configurable retry attempts (`tool_retry_attempts`).

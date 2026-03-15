# AGENTS.md

Project instructions for Codex and other AI agents working on this repository.

## What is Aleph

Aleph is an MCP server implementing Recursive Language Models (RLMs). It loads large data into RAM as in-memory contexts, letting LLMs explore data via search/peek/code execution without consuming the context window. Published on PyPI as `aleph-rlm`.

## Build & Test

```bash
pip install -e ".[dev,mcp]"
pytest tests/ -v          # ~426 tests, ~5s
ruff check aleph/ tests/  # linter
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

## Architecture

- **MCP Server**: `aleph/mcp/local_server.py` (~150K chars) — 30+ tools, recipe engine, sub-query orchestration
- **Core RLM Loop**: `aleph/core.py` — load context, LLM loop, code execution, convergence
- **REPL Sandbox**: `aleph/repl/sandbox.py` + `helpers.py` — sandboxed exec_python with 100+ helpers
- **Sub-Query**: `aleph/sub_query/` — CLI backends (kimi, claude, codex, gemini) and API backend
- **Recipes**: `aleph/mcp/recipes.py` — declarative pipeline validation and estimation

## Key Files

| File | Purpose |
|------|---------|
| `aleph/mcp/local_server.py` | MCP server, tool registration, recipe execution |
| `aleph/repl/helpers.py` | REPL helpers, Recipe DSL |
| `aleph/repl/sandbox.py` | Sandbox, async bridge, sub_query injection |
| `aleph/sub_query/cli_backend.py` | CLI sub-agent spawning |
| `aleph/sub_query/__init__.py` | Backend detection, SubQueryConfig |
| `aleph/mcp/recipes.py` | Recipe schema validation |
| `aleph/core.py` | Core Aleph RLM loop |

## Entry Points

| Command | Module | Purpose |
|---------|--------|---------|
| `aleph` | `aleph.mcp.local_server:main` | MCP server (primary) |
| `aleph-rlm` | `aleph.cli:main` | Installer/configurator |

## Gotchas

- SandboxConfig timeout field is `timeout_seconds` (not `timeout`)
- Load files with absolute paths when using MCP `load_file` tool
- Non-zero CLI exit codes return failure (stdout content is not treated as success)
- `sub_query_batch`/`sub_query_map` parallel path uses raw async fn to avoid event loop deadlock
- MCP server needs `/mcp` restart in Claude Code or Codex restart after code changes

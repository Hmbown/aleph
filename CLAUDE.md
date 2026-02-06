# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Aleph

Aleph is an MCP server implementing Recursive Language Models (RLMs). It loads large data (up to ~1GB) into RAM as in-memory contexts, letting LLMs explore data via search/peek/code execution without consuming the context window. Only results enter the LLM prompt, never raw content. Published on PyPI as `aleph-rlm`.

## Build & Development Commands

```bash
# Install for development (Python 3.10+ required)
pip install -e ".[dev,mcp]"

# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_helpers.py -v

# Run a specific test class or method
pytest tests/test_helpers.py::TestPeek::test_peek_full -v

# Type checking
mypy aleph/

# Version is in two places — sync with:
python scripts/sync_versions.py
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no need for `@pytest.mark.asyncio`). CI runs on Python 3.10, 3.11, 3.12.

## Architecture

### Core RLM Loop (`aleph/core.py`)

The `Aleph` class implements the RLM pattern: load context into a sandboxed REPL variable (`ctx`), send query+metadata to an LLM, then loop — parsing the LLM response for code blocks or `FINAL(...)` answers, executing code in the sandbox, feeding results back — until the answer converges or the budget is exhausted.

### MCP Server (`aleph/mcp/local_server.py`)

`AlephMCPServerLocal` is the primary interface. It exposes 30+ tools to MCP clients (Claude Desktop, Cursor, VSCode, Claude Code). Entry point: `aleph` command. Key flags: `--enable-actions` (filesystem/shell tools), `--workspace-mode any`, `--tool-docs concise`.

Tool categories: context management (load/list/diff/peek/search), computation (exec_python, get_variable), reasoning (think, evaluate_progress, summarize_so_far, finalize), recursion (sub_query, sub_aleph), action tools (read_file, write_file, run_command, rg_search, run_tests), recipes (run_recipe, compile_recipe), and remote MCP orchestration.

### REPL Sandbox (`aleph/repl/sandbox.py`, `aleph/repl/helpers.py`)

`REPLEnvironment` executes Python code from the LLM in a best-effort sandbox. It blocks dangerous builtins (`eval`, `exec`, `open`, `__import__`) and restricts imports to an allowlist. `helpers.py` provides 100+ built-in functions (peek, search, extract_*, chunk, cite, etc.) injected into the REPL namespace.

### Provider Abstraction (`aleph/providers/`)

`LLMProvider` protocol in `base.py` with implementations: `AnthropicProvider`, `OpenAIProvider`, `CLIProvider` (for local claude/codex/gemini CLIs). Factory: `get_provider()` in `registry.py`.

### Sub-Query Recursion (`aleph/sub_query/`)

Spawns independent sub-agents via CLI backends (claude, codex, gemini) or OpenAI-compatible API. Supports session sharing where sub-agents access the parent's live Aleph contexts via a streamable HTTP endpoint.

### Configuration (`aleph/config.py`)

`AlephConfig` dataclass loads from env vars (`ALEPH_*`), YAML/JSON files, or dicts. `create_aleph()` is the factory that wires config → provider → `Aleph` instance. Version is declared in both `pyproject.toml` and `aleph/__init__.py`.

### Key Type Definitions (`aleph/types.py`)

All core types: `Budget`, `ExecutionResult`, `AlephResponse`, `TrajectoryStep`, `ParsedAction`, `ActionType`, `ContextType`. The library is intentionally type-rich for mypy/pyright compatibility.

## Test Fixtures (in `tests/conftest.py`)

- `sandbox_config` — `SandboxConfig` with 5s timeout
- `repl` — `REPLEnvironment` with context `"test context for unit tests"`
- `repl_multiline` — `REPLEnvironment` with 6-line context

## Entry Points

| Command | Module | Purpose |
|---------|--------|---------|
| `aleph` | `aleph.mcp.local_server:main` | MCP server (primary) |
| `aleph-rlm` | `aleph.cli:main` | Installer/configurator/CLI runner |
| `alef` | `aleph.alef_cli:main` | Deprecated CLI |

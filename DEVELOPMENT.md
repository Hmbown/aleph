# Development Guide

Architecture and development workflow for Aleph.

---

## Overview

Aleph is an MCP server implementing the
[Recursive Language Model](https://arxiv.org/abs/2512.24601) (RLM) paradigm for
document analysis. Instead of stuffing context into prompts, Aleph stores
documents in a sandboxed Python REPL and provides tools for iterative
exploration.

Aleph operates in two modes: **(1) the RLM Core Loop**, where the LLM
iteratively writes and executes code in a REPL to reason over context, and
**(2) MCP Tool Server mode**, where an external client (Cursor, Claude Desktop,
Codex, etc.) calls Aleph tools directly without running the internal loop.

---

## Project Structure

```
aleph/
├── core.py              # Main Aleph class, RLM loop, message handling
├── types.py             # Dataclasses: Budget, AlephResponse, TrajectoryStep
├── config.py            # AlephConfig, create_aleph() factory
├── cli.py               # CLI entry points (aleph-rlm install/doctor)
├── mcp/
│   ├── local_server.py  # MCP server (main entry point)
│   ├── tool_registry.py # Tool registration helpers
│   ├── actions.py       # Action tools (read/write/run)
│   ├── recipes.py       # Recipe schema validation
│   ├── session.py       # Session serialization
│   ├── workspace.py     # Workspace root detection
│   └── server.py        # Compatibility entry point (aliases local_server)
├── repl/
│   ├── sandbox.py       # REPLEnvironment -- sandboxed code execution
│   └── helpers.py       # 100+ helper functions (peek, search, extract_*)
├── sub_query/
│   ├── __init__.py      # SubQueryConfig, detect_backend()
│   ├── cli_backend.py   # Claude / Codex / Gemini / Kimi CLI spawning
│   ├── codex_mcp_backend.py  # Codex MCP-mode sub-queries
│   └── api_backend.py   # OpenAI-compatible API calls
├── providers/
│   ├── base.py          # LLMProvider protocol
│   ├── registry.py      # get_provider() factory
│   ├── anthropic.py     # Anthropic provider
│   ├── openai.py        # OpenAI provider
│   ├── llamacpp.py      # Local llama.cpp provider
│   └── cli.py           # CLI provider (claude/codex/gemini)
└── prompts/
    └── system.py        # Default system prompt template
```

---

## Development Setup

```bash
# Clone and install in development mode
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e ".[dev,mcp]"

# Run tests
python3 -m pytest -q

# Run MCP server locally (with action tools enabled)
aleph --enable-actions --tool-docs concise
```

---

## Architecture

### Core Loop (`core.py`)

The `Aleph` class implements the RLM execution loop:

1. Context is stored in a sandboxed REPL namespace (`ctx`)
2. LLM receives metadata about context (format, size, preview) -- not full
   content
3. LLM writes Python code blocks to explore via helper functions
4. Aleph executes code, feeds truncated output back
5. Loop continues until LLM emits `FINAL(answer)` or `FINAL_VAR(variable_name)`

### MCP Server (`mcp/local_server.py`)

The primary entry point for IDE integration. Exposes tools:

| Category            | Tools                                                             |
|---------------------|-------------------------------------------------------------------|
| **Context**         | `load_context`, `peek_context`, `search_context`                  |
| **Compute**         | `exec_python`, `get_variable`                                     |
| **Recursion**       | `sub_query` (RLM-style recursive calls)                           |
| **Reasoning**       | `think`, `evaluate_progress`, `summarize_so_far`                  |
| **Output**          | `finalize`, `get_evidence`, `get_status`                          |
| **Actions**         | `run_command`, `read_file`, `write_file`, `run_tests`             |

### Sandbox (`repl/sandbox.py`)

The `REPLEnvironment` provides a sandboxed Python execution environment:

- **AST validation:** blocks dunder access, forbidden builtins
- **Import whitelist:** `re`, `json`, `csv`, `math`, `statistics`,
  `collections`, `itertools`, `functools`, `datetime`, `textwrap`, `difflib`,
  `random`, `string`, `hashlib`, `base64`, `urllib.parse`, `html`
- **Output truncation:** prevents token explosions
- **Helper injection:** 100+ functions for document analysis

The sandbox is best-effort, not hardened. For untrusted input, use container
isolation.

### Sub-Query System (`sub_query/`)

Enables RLM-style recursive reasoning:

```python
# Backend selection precedence:
# 1. SubQueryConfig.backend when it is set to a concrete backend
# 2. ALEPH_SUB_QUERY_BACKEND env var (explicit override)
# 3. API (if credentials available) -- preferred auto-selected backend
# 4. codex CLI (if installed) -- fallback
# claude, gemini, and kimi are available only when explicitly selected.
```

- **CLI backend:** spawns subprocess, passes prompt via stdin or temp file
- **API backend:** OpenAI-compatible HTTP calls (any provider with
  `/v1/chat/completions`)

### Budget System (`types.py`)

`Budget` dataclass controls resource limits:

```python
@dataclass
class Budget:
    max_tokens: int = 100_000
    max_cost_usd: float = 1.0
    max_iterations: int = 100
    max_depth: int = 5
    max_wall_time_seconds: float = 300.0
    max_sub_queries: int = 50
```

`BudgetStatus` tracks consumption and is checked at each iteration.

### Provider Protocol (`providers/base.py`)

Custom providers must implement:

```python
class LLMProvider(Protocol):
    def complete(self, messages, model, **kwargs) -> tuple[str, int, int, float]:
        """Returns (response_text, input_tokens, output_tokens, cost_usd)"""

    def count_tokens(self, text: str, model: str) -> int: ...
    def get_context_limit(self, model: str) -> int: ...
    def get_output_limit(self, model: str) -> int: ...
```

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=aleph --cov-report=term-missing

# Run specific test file
pytest tests/test_sub_query.py

# Run tests matching pattern
pytest -k "test_search"
```

---

## Code Style

- Python 3.10+ with type hints
- Linted with `ruff`

```bash
ruff check aleph tests
```

---

## Adding a New Tool

1. Add the tool function in `mcp/local_server.py` inside `_register_tools()`
2. Decorate with `@self.server.tool()`
3. Include comprehensive docstring (shown to AI users)
4. Update `_Session` if tool needs state tracking
5. Add tests in `tests/`

Example:

```python
@self.server.tool()
async def my_new_tool(
    arg1: str,
    arg2: int = 10,
    context_id: str = "default",
) -> str:
    """One-line description.

    Longer description of what this tool does.

    Args:
        arg1: Description
        arg2: Description (default: 10)
        context_id: Session identifier

    Returns:
        Description of return value
    """
    session = self._sessions.get(context_id)
    if not session:
        return f"Error: No context loaded with ID '{context_id}'"

    result = do_something(arg1, arg2)
    return f"## Result\n\n{result}"
```

---

## Adding a New Helper

1. Add the function in `repl/helpers.py`
2. Add to `HELPER_FUNCTIONS` dict at bottom of file
3. Add tests in `tests/test_helpers.py`

Example:

```python
def my_helper(ctx: str, arg: int = 5) -> list[str]:
    """One-line description.

    Args:
        ctx: The context string
        arg: Description (default: 5)

    Returns:
        List of results
    """
    # Implementation using ctx
    return results

# At bottom of file:
HELPER_FUNCTIONS = {
    # ... existing helpers ...
    "my_helper": my_helper,
}
```

---

## Environment Variables

| Variable                    | Purpose                                                    |
|-----------------------------|------------------------------------------------------------|
| `ALEPH_SUB_QUERY_BACKEND`  | Force sub-query backend: `api`, `claude`, `codex`, `gemini`, `kimi`, `auto` |
| `ALEPH_SUB_QUERY_API_KEY`  | API key (fallback: `OPENAI_API_KEY`)                       |
| `ALEPH_SUB_QUERY_URL`      | API base URL (fallback: `OPENAI_BASE_URL`)                 |
| `ALEPH_SUB_QUERY_MODEL`    | Model name (required for API backend)                      |
| `ALEPH_MAX_ITERATIONS`     | Iteration limit                                            |
| `ALEPH_MAX_COST`           | Cost limit in USD                                          |

---

## Release Process

1. Update version in `pyproject.toml`
2. Sync versioned files: `python scripts/sync_versions.py`
3. Update `CHANGELOG.md`
4. Run full test suite: `pytest`
5. Build locally: `python -m build`
6. Push `main`
7. Create a GitHub release with tag `vX.Y.Z`

Publishing to PyPI is handled by `.github/workflows/publish.yml` when the
GitHub release is published.

---

## Related Documentation

| Document                                              | Description                      |
|-------------------------------------------------------|----------------------------------|
| [README.md](README.md)                                | User documentation               |
| [docs/prompts/aleph.md](docs/prompts/aleph.md)       | Workflow prompt + tool reference  |
| [CHANGELOG.md](CHANGELOG.md)                         | Release notes                    |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md)       | Full configuration reference     |

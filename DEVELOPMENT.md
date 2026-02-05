# Development Guide

Architecture and development workflow for Aleph.

---

## Overview

Aleph is an MCP server implementing the
[Recursive Language Model](https://arxiv.org/abs/2512.24601) (RLM) paradigm for
document analysis. Instead of stuffing context into prompts, Aleph stores
documents in a sandboxed Python REPL and provides tools for iterative
exploration.

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
│   └── server.py        # Compatibility entry point (aliases local_server)
├── repl/
│   ├── sandbox.py       # REPLEnvironment -- sandboxed code execution
│   └── helpers.py       # 100+ helper functions (peek, search, extract_*)
├── sub_query/
│   ├── __init__.py      # SubQueryConfig, detect_backend()
│   ├── cli_backend.py   # Claude / Codex / Gemini CLI spawning
│   └── api_backend.py   # OpenAI-compatible API calls
├── providers/
│   ├── base.py          # LLMProvider protocol
│   ├── anthropic.py     # Anthropic provider
│   └── openai.py        # OpenAI provider
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
# Backend detection priority (when backend="auto"):
# 1. ALEPH_SUB_QUERY_BACKEND env var (explicit override)
# 2. API (if ALEPH_SUB_QUERY_API_KEY or OPENAI_API_KEY set)
# 3. claude CLI (if installed)
# 4. codex CLI (if installed)
# 5. gemini CLI (if installed)
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
- Formatted with `black` and `isort`
- Linted with `ruff`

```bash
# Format
black aleph tests
isort aleph tests

# Lint
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
| `ALEPH_SUB_QUERY_BACKEND`  | Force sub-query backend: `api`, `claude`, `codex`, `gemini`|
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
5. Build: `python -m build`
6. Upload to PyPI: `twine upload dist/*`
7. Tag release: `git tag v0.x.0 && git push --tags`

---

## Related Documentation

| Document                                              | Description                      |
|-------------------------------------------------------|----------------------------------|
| [README.md](README.md)                                | User documentation               |
| [docs/prompts/aleph.md](docs/prompts/aleph.md)       | Workflow prompt + tool reference  |
| [CHANGELOG.md](CHANGELOG.md)                         | Release notes                    |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md)       | Full configuration reference     |

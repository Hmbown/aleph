# CLAUDE.md

This file provides guidance when working with code in this repository.

## Overview

Aleph is an MCP server for **Recursive Language Model (RLM)** reasoning over documents. Instead of cramming context into prompts, Aleph stores context in a sandboxed Python REPL (as variable `ctx`) and lets the model iteratively explore it.

**Key feature**: `sub_query` tool enables RLM-style recursive sub-agent calls for chunked analysis.

## Development Commands

```bash
# Install (development)
pip install -e '.[mcp]'

# Run tests
python3 -m pytest -q

# Run local MCP server (API-free, for IDE integration)
aleph-mcp-local --enable-actions
```

## Architecture

### Core Loop (`aleph/core.py`)

The `Aleph` class implements the RLM execution loop:
1. Context is stored in a sandboxed REPL namespace (`ctx`)
2. LLM receives metadata about context (format, size, preview) - not the full context
3. LLM writes Python code blocks to explore context via helper functions
4. Aleph executes code, feeds truncated output back
5. Loop continues until LLM emits `FINAL(answer)` or `FINAL_VAR(variable_name)`

### Key Components

- **`aleph/core.py`**: Main `Aleph` class, RLM loop, message handling, sub-query/sub-aleph injection
- **`aleph/types.py`**: All dataclasses and type definitions (`Budget`, `AlephResponse`, `TrajectoryStep`, `ExecutionResult`, etc.)
- **`aleph/config.py`**: `AlephConfig` for loading from env/YAML/JSON, `create_aleph()` factory
- **`aleph/repl/sandbox.py`**: `REPLEnvironment` - sandboxed code execution with AST validation
- **`aleph/repl/helpers.py`**: REPL helper functions (`peek`, `lines`, `search`, `chunk`)
- **`aleph/providers/`**: Provider implementations (`anthropic.py`, `openai.py`) following `LLMProvider` protocol
- **`aleph/prompts/system.py`**: Default system prompt template with placeholders

### Provider Protocol (`aleph/providers/base.py`)

Custom providers must implement:
- `complete()` → `tuple[response_text, input_tokens, output_tokens, cost_usd]`
- `count_tokens(text, model)` → `int`
- `get_context_limit(model)` → `int`
- `get_output_limit(model)` → `int`

### Sandbox Security

The sandbox (`aleph/repl/sandbox.py`) is best-effort, not hardened:
- AST validation blocks dunder access, forbidden builtins (`eval`, `exec`, `open`, etc.)
- Import whitelist: `re`, `json`, `csv`, `math`, `statistics`, `collections`, `itertools`, `functools`, `datetime`, `textwrap`, `difflib`
- Output truncation to prevent token explosions

### Budget System

`Budget` dataclass controls resource limits:
- `max_tokens`, `max_cost_usd`, `max_iterations`, `max_depth`, `max_wall_time_seconds`, `max_sub_queries`

`BudgetStatus` tracks consumption and is checked at each iteration.

### Sub-calls (RLM Pattern)

The `sub_query` MCP tool enables recursive sub-agent calls:

```python
# In exec_python - chunk and analyze
chunks = chunk(100000)
for c in chunks:
    result = sub_query("Analyze this section:", context_slice=c)
```

**Backend priority** (auto-detected):
1. `claude` CLI - uses existing subscription, no extra API key
2. `codex` CLI - uses existing subscription
3. `aider` CLI
4. Mimo API - free fallback (requires `MIMO_API_KEY`)

## Environment Variables

- `MIMO_API_KEY`: Xiaomi MiMo API key (free at xiaomimimo.com)
- `OPENAI_BASE_URL`: API base URL (default: `https://api.xiaomimimo.com/v1`)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`: Provider API keys (for aleph.mcp.server)

## See Also

- `ALEPH.md` - Skill guide for using Aleph's RLM pattern
- `docs/1-4-update.md` - Implementation details for sub_query feature

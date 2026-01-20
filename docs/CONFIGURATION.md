# Aleph Configuration Guide

This guide covers all configuration options for Aleph, including environment variables, CLI flags, and programmatic configuration.

## Quick Reference

| Variable | Purpose | Default |
|----------|---------|---------|
| `ALEPH_WORKSPACE_ROOT` | Override workspace root detection | auto-detect |
| `ALEPH_SUB_QUERY_BACKEND` | Force sub-query backend | `auto` |
| `ALEPH_SUB_QUERY_API_KEY` | API key (fallback: `OPENAI_API_KEY`) | -- |
| `ALEPH_SUB_QUERY_URL` | API base URL (fallback: `OPENAI_BASE_URL`) | `https://api.openai.com/v1` |
| `ALEPH_SUB_QUERY_MODEL` | Model name (required for API) | -- |
| `ALEPH_MAX_ITERATIONS` | Maximum iterations per session | `100` |

## Sub-Query Configuration

The `sub_query` tool spawns independent sub-agents for recursive reasoning. It can use an API backend (OpenAI-compatible) or a local CLI backend (Claude, Codex, Gemini).

### Backend Priority (auto mode)

When `ALEPH_SUB_QUERY_BACKEND` is not set or set to `auto`:

1. **API** -- if any API credentials are available
2. **claude CLI** -- if installed (uses Claude Code subscription)
3. **codex CLI** -- if installed (uses OpenAI subscription)
4. **gemini CLI** -- if installed (uses Google Gemini subscription)

### Force a Specific Backend

```bash
# Force API backend
export ALEPH_SUB_QUERY_BACKEND=api

# Force Claude CLI
export ALEPH_SUB_QUERY_BACKEND=claude

# Force Codex CLI
export ALEPH_SUB_QUERY_BACKEND=codex

# Force Gemini CLI
export ALEPH_SUB_QUERY_BACKEND=gemini
```

### API Backend Configuration

The API backend supports any **OpenAI-compatible** endpoint. Configure with these environment variables:

| Variable | Purpose | Fallback |
|----------|---------|----------|
| `ALEPH_SUB_QUERY_API_KEY` | API key | `OPENAI_API_KEY` |
| `ALEPH_SUB_QUERY_URL` | Base URL | `OPENAI_BASE_URL` or `https://api.openai.com/v1` |
| `ALEPH_SUB_QUERY_MODEL` | Model name | (required) |

**Examples:**

```bash
# OpenAI
export ALEPH_SUB_QUERY_API_KEY=sk-...
export ALEPH_SUB_QUERY_MODEL=gpt-5.2-codex

# Groq (fast inference)
export ALEPH_SUB_QUERY_API_KEY=gsk_...
export ALEPH_SUB_QUERY_URL=https://api.groq.com/openai/v1
export ALEPH_SUB_QUERY_MODEL=llama-3.3-70b-versatile

# Together AI
export ALEPH_SUB_QUERY_API_KEY=...
export ALEPH_SUB_QUERY_URL=https://api.together.xyz/v1
export ALEPH_SUB_QUERY_MODEL=meta-llama/Llama-3-70b-chat-hf

# DeepSeek
export ALEPH_SUB_QUERY_API_KEY=...
export ALEPH_SUB_QUERY_URL=https://api.deepseek.com/v1
export ALEPH_SUB_QUERY_MODEL=deepseek-chat

# Ollama (local)
export ALEPH_SUB_QUERY_API_KEY=ollama  # any non-empty value
export ALEPH_SUB_QUERY_URL=http://localhost:11434/v1
export ALEPH_SUB_QUERY_MODEL=llama3.2

# LM Studio (local)
export ALEPH_SUB_QUERY_API_KEY=lm-studio  # any non-empty value
export ALEPH_SUB_QUERY_URL=http://localhost:1234/v1
export ALEPH_SUB_QUERY_MODEL=local-model
```

**Using OPENAI_* fallbacks:**

If you already have `OPENAI_API_KEY` and `OPENAI_BASE_URL` set, you only need to set the model:

```bash
export ALEPH_SUB_QUERY_MODEL=gpt-5.2-codex
```

### CLI Backend Notes

**Claude CLI (`claude`):**
- Requires Claude Code installed: `npm install -g @anthropic-ai/claude-code`
- Uses your existing Claude subscription (no extra API key)
- Spawns: `claude -p "prompt" --dangerously-skip-permissions`

**Codex CLI (`codex`):**
- Requires OpenAI Codex CLI installed
- Uses your existing OpenAI subscription
- Spawns: `codex exec --full-auto "prompt"`

**Gemini CLI (`gemini`):**
- Requires Gemini CLI installed: `npm install -g @google/gemini-cli`
- Uses your existing Google/Gemini subscription (free tier available)
- Spawns: `gemini -p "prompt"`

## MCP Server Configuration

### CLI Flags

```bash
# Basic usage
aleph

# With action tools enabled (file/command access)
aleph --enable-actions --tool-docs concise

# Custom timeout and output limits
aleph --timeout 60 --max-output 100000

# Custom file size limits (read/write)
aleph --enable-actions --max-file-size 2000000000 --max-write-bytes 200000000

# Require confirmation for action tools
aleph --enable-actions --tool-docs concise --require-confirmation

# Custom workspace root
aleph --enable-actions --tool-docs concise --workspace-root /path/to/project

# Allow any git repo (use absolute paths in tool calls)
aleph --enable-actions --tool-docs concise --workspace-mode git

# Full tool docs (larger MCP tool list payload)
aleph --tool-docs full
```

## Power Features (Default When Actions Enabled)

- `rg_search`: fast repo search (uses ripgrep if available)
- `semantic_search`: meaning-based search over loaded contexts
- `load_file`: smart loaders for PDF/DOCX/HTML/logs (+ .gz/.bz2/.xz)
- Memory packs: auto-save to `.aleph/memory_pack.json` and auto-load on startup
- Memory packs: `save_session(context_id="*")` and `load_session(path=...)` for manual control
- `tasks`: lightweight task tracking per context

### MCP Client Configuration

**Claude Desktop / Cursor / Windsurf:**

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions", "--tool-docs", "concise"],
      "env": {
        "ALEPH_SUB_QUERY_API_KEY": "${ALEPH_SUB_QUERY_API_KEY}",
        "ALEPH_SUB_QUERY_MODEL": "${ALEPH_SUB_QUERY_MODEL}"
      }
    }
  }
}
```

**Codex CLI (`~/.codex/config.toml`):**

```toml
[mcp_servers.aleph]
command = "aleph"
args = ["--enable-actions", "--tool-docs", "concise"]
```

## Sandbox Configuration

The Python sandbox can be configured programmatically:

```python
from aleph.repl.sandbox import SandboxConfig, REPLEnvironment

config = SandboxConfig(
    timeout_seconds=60.0,      # Code execution timeout
    max_output_chars=50000,    # Truncate output after this
)

repl = REPLEnvironment(
    context="your document here",
    context_var_name="ctx",
    config=config,
)
```

### Sandbox Security

The sandbox blocks:
- File system access (`open`, `os`, `pathlib`)
- Network access (`socket`, `urllib`, `requests`)
- Process spawning (`subprocess`, `os.system`)
- Dangerous builtins (`eval`, `exec`, `compile`)
- Dunder attribute access (`__class__`, `__globals__`, etc.)

Allowed imports:
- `re`, `json`, `csv`
- `math`, `statistics`
- `collections`, `itertools`, `functools`
- `datetime`, `textwrap`, `difflib`
- `random`, `string`
- `hashlib`, `base64`
- `urllib.parse`, `html`

## Budget Configuration

Control resource usage programmatically:

```python
from aleph.types import Budget

budget = Budget(
    max_tokens=100_000,        # Total token limit
    max_iterations=100,        # Iteration limit
    max_depth=5,               # Sub-query recursion depth
    max_wall_time_seconds=300, # Wall clock timeout
    max_sub_queries=50,        # Sub-query count limit
)
```

## Environment File

Create a `.env` file in your project root:

```bash
# Sub-query API configuration (OpenAI-compatible)
ALEPH_SUB_QUERY_API_KEY=sk-...
ALEPH_SUB_QUERY_MODEL=gpt-5.2-codex

# Optional: custom endpoint
# ALEPH_SUB_QUERY_URL=https://api.groq.com/openai/v1

# Or use CLI backend (no API key needed)
# ALEPH_SUB_QUERY_BACKEND=claude

# Resource limits
ALEPH_MAX_ITERATIONS=100

# MCP remote tool timeout (seconds)
ALEPH_REMOTE_TOOL_TIMEOUT=120
```

Load with your shell or tool of choice (e.g., `source .env`, `dotenv`, or IDE integration).

## Troubleshooting

### Sub-query not working

1. Check backend detection:
   ```bash
   # Which CLI tools are available?
   which claude codex

   # Are API credentials set?
   echo $ALEPH_SUB_QUERY_API_KEY $OPENAI_API_KEY
   ```

2. Force a specific backend to test:
   ```bash
   export ALEPH_SUB_QUERY_BACKEND=api
   export ALEPH_SUB_QUERY_API_KEY=sk-...
   export ALEPH_SUB_QUERY_MODEL=gpt-5.2-codex
   ```

3. Check logs for errors in the MCP client.

### Sandbox timeout

Increase the timeout:
```bash
aleph --timeout 120
```

### Output truncated

Increase the output limit:
```bash
aleph --max-output 100000
```

### Actions disabled

Enable action tools:
```bash
aleph --enable-actions --tool-docs concise
```

## See Also

- [README.md](../README.md) -- Overview and quick start
- [DEVELOPMENT.md](../DEVELOPMENT.md) -- Architecture and development
- [docs/prompts/aleph.md](../docs/prompts/aleph.md) -- Workflow prompt + tool reference

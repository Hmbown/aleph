# Aleph Configuration Guide

All configuration options for Aleph: environment variables, CLI flags, and
programmatic configuration.

---

## Quick Reference

| Variable                              | Purpose                                         | Default                       |
|---------------------------------------|-------------------------------------------------|-------------------------------|
| `ALEPH_WORKSPACE_ROOT`                | Override workspace root detection                | auto-detect                   |
| `ALEPH_SUB_QUERY_BACKEND`            | Force sub-query backend                          | `auto`                        |
| `ALEPH_SUB_QUERY_TIMEOUT`            | Sub-query timeout in seconds                     | CLI 300 / API 120             |
| `ALEPH_SUB_QUERY_SHARE_SESSION`      | Share MCP session with CLI sub-agents            | `false`                       |
| `ALEPH_SUB_QUERY_API_KEY`            | API key (fallback: `OPENAI_API_KEY`)             | --                            |
| `ALEPH_SUB_QUERY_URL`                | API base URL (fallback: `OPENAI_BASE_URL`)       | `https://api.openai.com/v1`   |
| `ALEPH_SUB_QUERY_MODEL`              | Model name (required for API)                    | --                            |
| `ALEPH_SUB_QUERY_HTTP_HOST`          | Host for shared MCP session                      | `127.0.0.1`                   |
| `ALEPH_SUB_QUERY_HTTP_PORT`          | Port for shared MCP session                      | `8765`                        |
| `ALEPH_SUB_QUERY_HTTP_PATH`          | Path for shared MCP session                      | `/mcp`                        |
| `ALEPH_SUB_QUERY_MCP_SERVER_NAME`    | Server name exposed to sub-agents                | `aleph_shared`                |
| `ALEPH_MAX_ITERATIONS`                | Maximum iterations per session                   | `100`                         |
| `ALEPH_MAX_DEPTH`                     | Maximum recursion depth for sub_aleph            | `2`                           |

---

## Sub-Query Configuration

The `sub_query` tool spawns independent sub-agents for recursive reasoning. It
can use an API backend (OpenAI-compatible) or a local CLI backend (Claude, Codex,
Gemini). Auto mode prioritizes CLI backends, then falls back to API.

### Environment Variables

| Variable                              | Description                                                   | Default                       |
|---------------------------------------|---------------------------------------------------------------|-------------------------------|
| `ALEPH_SUB_QUERY_BACKEND`            | Backend override (`auto`, `api`, `codex`, `gemini`, `claude`) | `auto`                        |
| `ALEPH_SUB_QUERY_TIMEOUT`            | Timeout in seconds for CLI + API sub-queries                  | CLI 300 / API 120             |
| `ALEPH_SUB_QUERY_SHARE_SESSION`      | Share MCP session with CLI sub-agents                         | `false`                       |
| `ALEPH_SUB_QUERY_HTTP_HOST`          | Host for shared MCP session                                   | `127.0.0.1`                   |
| `ALEPH_SUB_QUERY_HTTP_PORT`          | Port for shared MCP session                                   | `8765`                        |
| `ALEPH_SUB_QUERY_HTTP_PATH`          | Path for shared MCP session                                   | `/mcp`                        |
| `ALEPH_SUB_QUERY_MCP_SERVER_NAME`    | MCP server name exposed to sub-agents                         | `aleph_shared`                |
| `ALEPH_SUB_QUERY_API_KEY`            | API key for OpenAI-compatible providers                       | `OPENAI_API_KEY`              |
| `ALEPH_SUB_QUERY_URL`                | API base URL                                                  | `OPENAI_BASE_URL` or OpenAI   |
| `ALEPH_SUB_QUERY_MODEL`              | API model name                                                | (required)                    |
| `ALEPH_SUB_QUERY_VALIDATION_REGEX`   | Default validation regex for strict output                    | (unset)                       |
| `ALEPH_SUB_QUERY_MAX_RETRIES`        | Default retries after validation failure                      | `0`                           |
| `ALEPH_SUB_QUERY_RETRY_PROMPT`       | Retry prompt suffix                                           | (default text)                |

### Backend Priority (auto mode)

When `ALEPH_SUB_QUERY_BACKEND` is not set or set to `auto`:

1. **codex CLI** -- if installed (uses OpenAI subscription)
2. **gemini CLI** -- if installed (uses Google Gemini subscription)
3. **claude CLI** -- if installed (deprioritized in MCP/sandbox contexts)
4. **API** -- if any API credentials are available (fallback)

### Force a Specific Backend

```bash
export ALEPH_SUB_QUERY_BACKEND=api      # Force API backend
export ALEPH_SUB_QUERY_BACKEND=claude   # Force Claude CLI
export ALEPH_SUB_QUERY_BACKEND=codex    # Force Codex CLI
export ALEPH_SUB_QUERY_BACKEND=gemini   # Force Gemini CLI
export ALEPH_SUB_QUERY_BACKEND=auto     # Return to auto selection
```

### CLI Flags (sub-query)

These flags set environment variables before the MCP server starts:

```bash
aleph --sub-query-backend claude
aleph --sub-query-timeout 90
aleph --sub-query-share-session true

# Combined
aleph --sub-query-backend codex --sub-query-timeout 120 --sub-query-share-session false
```

### Runtime Configuration

**MCP tool:**

```python
mcp__aleph__configure(sub_query_backend="claude")
mcp__aleph__configure(sub_query_timeout=90, sub_query_share_session=True)
```

**REPL helpers:**

```python
set_backend("gemini")
get_config()
```

### Runtime Switching (No Restart)

Users can ask the LLM to switch backends naturally:

- "Use the Claude backend for sub-queries"
- "Switch to Gemini"
- "aleph sub-query codex"

The LLM calls `set_backend("claude")` or `configure(sub_query_backend="claude")`
-- takes effect immediately.

### Backend Comparison

| Backend   | Speed                       | Cost                  | Capabilities                    |
|-----------|-----------------------------|-----------------------|---------------------------------|
| `api`     | Variable (provider-dependent)| Usage-based           | Custom models, full control     |
| `codex`   | Fast                        | Subscription          | Strong code reasoning           |
| `gemini`  | Fast                        | Free tier / subscription | Google ecosystem integration |
| `claude`  | Medium                      | Subscription          | Highest quality responses       |

### API Backend Configuration

The API backend supports any **OpenAI-compatible** endpoint:

| Variable                  | Purpose   | Fallback                                      |
|---------------------------|-----------|-----------------------------------------------|
| `ALEPH_SUB_QUERY_API_KEY` | API key  | `OPENAI_API_KEY`                               |
| `ALEPH_SUB_QUERY_URL`     | Base URL | `OPENAI_BASE_URL` or `https://api.openai.com/v1`|
| `ALEPH_SUB_QUERY_MODEL`   | Model    | (required)                                     |

**Precedence:** `ALEPH_SUB_QUERY_URL` overrides `OPENAI_BASE_URL`. If neither
is set, Aleph uses `https://api.openai.com/v1`.

**Examples:**

```bash
# OpenAI
export ALEPH_SUB_QUERY_API_KEY=sk-...
export ALEPH_SUB_QUERY_MODEL=your-model-name

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

# Ollama (local) -- make sure the server is running
export ALEPH_SUB_QUERY_API_KEY=ollama   # any non-empty value
export ALEPH_SUB_QUERY_URL=http://localhost:11434/v1
export ALEPH_SUB_QUERY_MODEL=llama3.2

# LM Studio (local) -- make sure the server is running
export ALEPH_SUB_QUERY_API_KEY=lm-studio   # any non-empty value
export ALEPH_SUB_QUERY_URL=http://localhost:1234/v1
export ALEPH_SUB_QUERY_MODEL=local-model
```

**Using OPENAI_* fallbacks:**

If you already have `OPENAI_API_KEY` and `OPENAI_BASE_URL` set, you only need
the model:

```bash
export ALEPH_SUB_QUERY_MODEL=your-model-name
```

### CLI Backend Notes

| Backend   | Install                                              | Spawns                                         |
|-----------|------------------------------------------------------|------------------------------------------------|
| `claude`  | `npm install -g @anthropic-ai/claude-code`           | `claude -p "prompt" --dangerously-skip-permissions` |
| `codex`   | OpenAI Codex CLI                                     | `codex exec --full-auto "prompt"`              |
| `gemini`  | `npm install -g @google/gemini-cli`                  | `gemini -y "prompt"`                           |

---

## Sub-Aleph (Nested Recursion)

The `sub_aleph` tool runs a full Aleph loop inside another Aleph run. Control
recursion depth with:

- `ALEPH_MAX_DEPTH` (default `2`) for how many nested levels are allowed
  - Example: set `ALEPH_MAX_DEPTH=3` to allow one extra nested layer

`sub_aleph` uses the standard Aleph provider/model settings:
`ALEPH_PROVIDER`, `ALEPH_MODEL`, `ALEPH_SUB_MODEL`, `ALEPH_API_KEY`.

---

## MCP Server Configuration

### CLI Flags

```bash
# Basic usage
aleph

# With action tools enabled (file/command access)
aleph --enable-actions --tool-docs concise

# Custom timeout and output limits
aleph --timeout 60 --max-output 100000

# Sub-query backend configuration
aleph --sub-query-backend claude --sub-query-timeout 90 --sub-query-share-session true

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

**Workspace auto-detection:** if `--workspace-root` is not set, Aleph will:

1. Use `ALEPH_WORKSPACE_ROOT` if provided
2. Prefer `PWD` (falls back to `INIT_CWD`) when present
3. Fall back to `os.getcwd()` and walk up to the nearest `.git` root

---

## Power Features (Default When Actions Enabled)

| Feature              | Description                                                          |
|----------------------|----------------------------------------------------------------------|
| `rg_search`          | Fast repo search (uses ripgrep if available)                         |
| `semantic_search`    | Meaning-based search over loaded contexts                            |
| `load_file`          | Smart loaders for PDF/DOCX/HTML/logs (+ .gz/.bz2/.xz)               |
| Memory packs         | Auto-save to `.aleph/memory_pack.json` and auto-load on startup      |
| `save_session`       | `save_session(context_id="*")` and `load_session(path=...)` for manual control |
| `tasks`              | Lightweight task tracking per context                                |

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

---

## Sandbox Configuration

The Python sandbox can be configured programmatically:

```python
from aleph.repl.sandbox import SandboxConfig, REPLEnvironment

config = SandboxConfig(
    timeout_seconds=60.0,       # Code execution timeout
    max_output_chars=50000,     # Truncate output after this
)

repl = REPLEnvironment(
    context="your document here",
    context_var_name="ctx",
    config=config,
)
```

### Sandbox Security

**Blocked:**

- File system access (`open`, `os`, `pathlib`)
- Network access (`socket`, `urllib`, `requests`)
- Process spawning (`subprocess`, `os.system`)
- Dangerous builtins (`eval`, `exec`, `compile`)
- Dunder attribute access (`__class__`, `__globals__`, etc.)

**Allowed imports:**

`re`, `json`, `csv`, `math`, `statistics`, `collections`, `itertools`,
`functools`, `datetime`, `textwrap`, `difflib`, `random`, `string`, `hashlib`,
`base64`, `urllib.parse`, `html`

---

## Budget Configuration

Control resource usage programmatically:

```python
from aleph.types import Budget

budget = Budget(
    max_tokens=100_000,         # Total token limit
    max_iterations=100,         # Iteration limit
    max_depth=5,                # Recursive depth (sub_aleph/sub_query)
    max_wall_time_seconds=300,  # Wall clock timeout
    max_sub_queries=50,         # Sub-query count limit
)
```

---

## Environment File

Create a `.env` file in your project root:

```bash
# Sub-query API configuration (OpenAI-compatible)
ALEPH_SUB_QUERY_API_KEY=sk-...
ALEPH_SUB_QUERY_MODEL=your-model-name

# Optional: custom endpoint
# ALEPH_SUB_QUERY_URL=https://api.groq.com/openai/v1

# Or use CLI backend (no API key needed)
# ALEPH_SUB_QUERY_BACKEND=claude

# Optional: strict output validation + retries
# ALEPH_SUB_QUERY_VALIDATION_REGEX=^[-*]
# ALEPH_SUB_QUERY_MAX_RETRIES=2
# ALEPH_SUB_QUERY_RETRY_PROMPT=Return ONLY bullet lines starting with "- ".

# Resource limits
ALEPH_MAX_ITERATIONS=100

# MCP remote tool timeout (seconds)
ALEPH_REMOTE_TOOL_TIMEOUT=120
```

Load with your shell or tool of choice (e.g., `source .env`, `dotenv`, or IDE
integration).

---

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
   export ALEPH_SUB_QUERY_MODEL=your-model-name
   ```

3. Check logs for errors in the MCP client.

### Sub-query timeout

Increase the timeout and/or reduce context slice size:

```bash
export ALEPH_SUB_QUERY_TIMEOUT=120
aleph --sub-query-timeout 120
```

### Sandbox timeout

```bash
aleph --timeout 120
```

### Output truncated

```bash
aleph --max-output 100000
```

### Actions disabled

```bash
aleph --enable-actions --tool-docs concise
```

---

## See Also

| Document                                              | Description                      |
|-------------------------------------------------------|----------------------------------|
| [README.md](../README.md)                             | Overview and quick start         |
| [DEVELOPMENT.md](../DEVELOPMENT.md)                   | Architecture and development     |
| [docs/prompts/aleph.md](prompts/aleph.md)             | Workflow prompt + tool reference  |

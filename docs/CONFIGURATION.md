# Aleph Configuration Guide

All configuration options for Aleph: environment variables, CLI flags, and
programmatic configuration.

---

## Quick Reference

| Variable                              | Purpose                                         | Default                       |
|---------------------------------------|-------------------------------------------------|-------------------------------|
| `ALEPH_WORKSPACE_ROOT`                | Override workspace root detection                | auto-detect                   |
| `ALEPH_CONTEXT_POLICY`               | Context policy (`trusted` or `isolated`)         | `trusted`                     |
| `ALEPH_OUTPUT_FEEDBACK`              | REPL output mode (`full` or `metadata`)          | `full`                        |
| `ALEPH_MAX_RECIPE_CONCURRENCY`       | Max parallel `map_sub_query` tasks in recipes    | `10`                          |
| `ALEPH_SUB_QUERY_BACKEND`            | Force sub-query backend                          | `auto`                        |
| `ALEPH_SUB_QUERY_TIMEOUT`            | Sub-query timeout in seconds                     | CLI 300 / API 120             |
| `ALEPH_SUB_QUERY_SHARE_SESSION`      | Share MCP session with CLI sub-agents            | `false`                       |
| `ALEPH_SUB_QUERY_CLAUDE_MODEL`       | Claude CLI model alias/name                      | `opus`                        |
| `ALEPH_SUB_QUERY_CLAUDE_EFFORT`      | Claude CLI effort                                | `low`                         |
| `ALEPH_SUB_QUERY_CODEX_MODE`         | Codex backend mode (`exec` or `mcp`)             | `mcp`                         |
| `ALEPH_SUB_QUERY_CODEX_MODEL`        | Codex MCP model override                         | `gpt-5.4`                     |
| `ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT` | Codex MCP reasoning effort                    | `low`                         |
| `ALEPH_SUB_QUERY_CODEX_PROFILE`      | Codex MCP profile override                       | --                            |
| `ALEPH_SUB_QUERY_API_KEY`            | API key (fallback: `OPENAI_API_KEY`)             | --                            |
| `ALEPH_SUB_QUERY_URL`                | API base URL (fallback: `OPENAI_BASE_URL`)       | `https://api.openai.com/v1`   |
| `ALEPH_SUB_QUERY_MODEL`              | Model name (required for API)                    | --                            |
| `ALEPH_SUB_QUERY_HTTP_HOST`          | Host for shared MCP session                      | `127.0.0.1`                   |
| `ALEPH_SUB_QUERY_HTTP_PORT`          | Port for shared MCP session                      | `8765`                        |
| `ALEPH_SUB_QUERY_HTTP_PATH`          | Path for shared MCP session                      | `/mcp`                        |
| `ALEPH_SUB_QUERY_MCP_SERVER_NAME`    | Server name exposed to sub-agents                | `aleph_shared`                |
| `ALEPH_PROVIDER`                      | LLM provider (`anthropic`, `openai`, `llamacpp`, `cli`) | `anthropic`             |
| `ALEPH_MODEL`                         | Model name for the RLM loop                      | `claude-sonnet-4-20250514`    |
| `ALEPH_BASE_URL`                      | Override provider's default API endpoint          | --                            |
| `ALEPH_LLAMACPP_URL`                 | llama-server URL                                  | `http://127.0.0.1:8080`      |
| `ALEPH_LLAMACPP_MODEL`              | Path to GGUF model file (for auto-start)          | --                            |
| `ALEPH_LLAMACPP_CTX`                | Context size in tokens                             | `8192`                        |
| `ALEPH_LLAMACPP_GPU_LAYERS`         | Layers to offload to GPU                           | `99` (all)                    |
| `ALEPH_LLAMACPP_AUTO_START`         | Auto-start llama-server if not running             | `true`                        |
| `ALEPH_MAX_ITERATIONS`                | Maximum iterations per session                   | `100`                         |
| `ALEPH_MAX_DEPTH`                     | Maximum recursion depth for sub_aleph            | `2`                           |

---

## Local Models (llama.cpp)

The `llamacpp` provider runs the RLM loop against a local
[llama.cpp](https://github.com/ggml-org/llama.cpp) server. No API key, no
network, zero cost.

### Setup

Install llama.cpp:

```bash
brew install llama.cpp          # Mac
winget install ggml.LlamaCpp    # Windows
```

Download a GGUF model (any will work — Q4_K_M for speed, Q8_0 for quality):

```bash
# Example: Qwen 3.5 9B Q8_0 (~9 GB)
huggingface-cli download Qwen/Qwen3.5-9B-GGUF qwen3.5-9b-q8_0.gguf --local-dir ./models
```

### Option A: Start the server yourself

```bash
llama-server -m ./models/qwen3.5-9b-q8_0.gguf -c 16384 -ngl 99 --port 8080
```

Then configure Aleph:

```bash
export ALEPH_PROVIDER=llamacpp
export ALEPH_LLAMACPP_URL=http://127.0.0.1:8080
export ALEPH_MODEL=local
aleph
```

### Option B: Let Aleph manage the server

```bash
export ALEPH_PROVIDER=llamacpp
export ALEPH_LLAMACPP_MODEL=./models/qwen3.5-9b-q8_0.gguf
export ALEPH_LLAMACPP_CTX=16384
export ALEPH_MODEL=local
aleph
```

Aleph starts `llama-server` on first use and shuts it down when the MCP
session ends.

### Environment Variables

| Variable                      | Description                              | Default                    |
|-------------------------------|------------------------------------------|----------------------------|
| `ALEPH_LLAMACPP_URL`        | Server URL                                | `http://127.0.0.1:8080`   |
| `ALEPH_LLAMACPP_MODEL`      | Path to `.gguf` file (enables auto-start) | --                         |
| `ALEPH_LLAMACPP_CTX`        | Context size in tokens                    | `8192`                     |
| `ALEPH_LLAMACPP_GPU_LAYERS` | GPU layers (`-ngl`), `99` = all           | `99`                       |
| `ALEPH_LLAMACPP_AUTO_START` | Start server if not running               | `true`                     |

### Notes

- **Model name doesn't matter.** Set `ALEPH_MODEL=local` (or any string).
  llama-server always uses the model it was started with.
- **Reasoning models work.** Qwen 3.5, QwQ, and other models that put
  chain-of-thought in a `reasoning_content` field are handled automatically.
- **Cost is always $0.** Token counts are tracked for budgeting but cost is
  reported as zero.
- **Cross-platform.** Same setup on Mac (ARM/Intel), Windows, and Linux.
  `llama-server` binary name is the same everywhere.

---

## Sub-Query Configuration

The `sub_query` tool spawns independent sub-agents for recursive reasoning. It
can use an API backend (OpenAI-compatible) or a local CLI backend. Codex is the
only auto-selected CLI backend; `claude`, `gemini`, and `kimi` remain available
as explicit experimental overrides.

### Environment Variables

| Variable                              | Description                                                   | Default                       |
|---------------------------------------|---------------------------------------------------------------|-------------------------------|
| `ALEPH_SUB_QUERY_BACKEND`            | Backend override (`auto`, `api`, `codex`, `gemini`, `kimi`, `claude`) | `auto`                        |
| `ALEPH_SUB_QUERY_TIMEOUT`            | Timeout in seconds for CLI + API sub-queries                  | CLI 300 / API 120             |
| `ALEPH_SUB_QUERY_SHARE_SESSION`      | Share MCP session with CLI sub-agents                         | `false`                       |
| `ALEPH_SUB_QUERY_CLAUDE_MODEL`       | Claude CLI model alias/name                                     | `opus`                        |
| `ALEPH_SUB_QUERY_CLAUDE_EFFORT`      | Claude CLI effort                                               | `low`                         |
| `ALEPH_SUB_QUERY_CODEX_MODE`         | Route codex through `codex exec` or `codex mcp-server`        | `mcp`                         |
| `ALEPH_SUB_QUERY_CODEX_MODEL`        | Codex MCP model override                                       | `gpt-5.4`                     |
| `ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT` | Codex MCP reasoning effort                                | `low`                         |
| `ALEPH_SUB_QUERY_CODEX_PROFILE`      | Codex MCP profile override                                     | (unset)                       |
| `ALEPH_SUB_QUERY_GEMINI_SANDBOX`     | Re-enable Gemini CLI sandboxing for sub-queries               | `false`                       |
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
2. **API** -- if any API credentials are available (fallback)

`gemini`, `claude`, and `kimi` remain available only when explicitly selected.

### Shared-Session Architecture

When `ALEPH_SUB_QUERY_SHARE_SESSION=true`, Aleph starts a local streamable HTTP
server and points the nested CLI back at that live Aleph session. That is what
lets the sub-agent use Aleph MCP tools (`search_context`, `peek_context`,
`exec_python`, etc.) instead of relying on a prompt-embedded slice.

The injection mechanism varies by CLI:

| Backend | How Aleph injects the live MCP server |
|---------|---------------------------------------|
| `codex` | Native Codex MCP config overrides via `codex mcp-server` |
| `claude` | Temp JSON file via `--mcp-config` and `--strict-mcp-config` |
| `gemini` | Temp JSON file via `GEMINI_CLI_SYSTEM_SETTINGS_PATH` |

Codex is the simplest and best-supported path because it accepts the live MCP
server natively and avoids temp-file isolation hacks.

### Install Profiles

The installer now asks for a sub-query profile up front instead of silently
pinning Codex.

```bash
aleph-rlm install
aleph-rlm install --profile claude
aleph-rlm install --profile codex
aleph-rlm install --profile portable
```

Profiles:

- `portable`: do not pin a nested backend
- `claude`: backend=`claude`, share-session=`true`, timeout=`300`, model=`opus`, effort=`low`
- `codex`: backend=`codex`, share-session=`true`, timeout=`300`, mode=`mcp`, model=`gpt-5.4`, reasoning=`low`
- `api`: backend=`api`, timeout=`300`

### Selection Precedence

Aleph resolves the sub-query backend in this order:

1. Programmatic config (`SubQueryConfig(backend=...)` or `configure(sub_query_backend=...)`)
2. `ALEPH_SUB_QUERY_BACKEND` when set to a concrete backend
3. Auto-detection: `codex` -> `api`

`aleph-rlm install` and `aleph-rlm configure` now make the nested backend an
explicit profile choice. Auto mode inside Aleph itself still resolves
`codex -> api`, but generated install configs no longer silently pin Codex.

### Force a Specific Backend

```bash
export ALEPH_SUB_QUERY_BACKEND=api      # Force API backend
export ALEPH_SUB_QUERY_BACKEND=claude   # Force Claude CLI
export ALEPH_SUB_QUERY_BACKEND=codex    # Force Codex CLI
export ALEPH_SUB_QUERY_BACKEND=gemini   # Force Gemini CLI
export ALEPH_SUB_QUERY_BACKEND=kimi     # Force Kimi CLI
export ALEPH_SUB_QUERY_BACKEND=auto     # Return to auto selection
```

### CLI Flags (sub-query)

These flags set environment variables before the MCP server starts:

```bash
aleph --sub-query-backend claude
aleph --sub-query-timeout 90
aleph --sub-query-share-session true
aleph --sub-query-claude-model opus --sub-query-claude-effort low
aleph --sub-query-codex-model gpt-5.4 --sub-query-codex-reasoning-effort low

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

| Backend   | Status in Aleph | What it is best for |
|-----------|-----------------|---------------------|
| `codex`   | First-class, validated default | Nested MCP sub-queries, shared-session recursion, exact-output and retry-sensitive work |
| `api`     | Supported fallback | OpenAI-compatible endpoints and custom hosted models |
| `claude`  | Explicit stable fallback | General sub-queries when Codex is unavailable; shared-session worked in live tests with clean isolation, but output is slightly more formatted and runs are stateless |
| `gemini`  | Explicit experimental override | General sub-queries with live Aleph MCP access; accurate but noisier, and depends on Aleph's headless JSON + extension-free launch |
| `kimi`    | Explicit experimental override | Available for manual use, but not validated as reliable in the current Aleph dogfood runs |

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
| `claude`  | `npm install -g @anthropic-ai/claude-code`           | `claude -p --model opus --effort low "prompt" --dangerously-skip-permissions --no-session-persistence --output-format json` |
| `codex`   | OpenAI Codex CLI                                     | `codex exec --full-auto "prompt"` or `codex mcp-server` |
| `gemini`  | `npm install -g @google/gemini-cli`                  | `gemini -y --sandbox=false --extensions "" -o json -p "prompt"` |

For nested Codex MCP sub-queries:

```bash
export ALEPH_SUB_QUERY_BACKEND=codex
export ALEPH_SUB_QUERY_CODEX_MODE=mcp
export ALEPH_SUB_QUERY_CODEX_MODEL=gpt-5.4
export ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT=low
export ALEPH_SUB_QUERY_SHARE_SESSION=true
```

Aleph starts that nested Codex path with `codex mcp-server -c mcp_servers={}`
so the internal Codex handle does not inherit unrelated Codex MCP servers.

For an all-Claude setup:

```bash
export ALEPH_SUB_QUERY_BACKEND=claude
export ALEPH_SUB_QUERY_CLAUDE_MODEL=opus
export ALEPH_SUB_QUERY_CLAUDE_EFFORT=low
export ALEPH_SUB_QUERY_SHARE_SESSION=true
```

Claude receives the live Aleph server through a temp `--mcp-config` file and
`--strict-mcp-config`, which keeps the nested run isolated from unrelated user
MCP servers.

For the explicit Gemini override, Aleph also launches Gemini with
`--extensions ""` so nested sub-queries do not inherit unrelated user
extensions from `~/.gemini`.

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

## Deployment Profiles

Aleph ships two context policies that control how aggressively the server
guards raw context at the MCP boundary. Choose based on your threat model.

### Trusted (default)

Low friction. Auto memory-pack save/load on startup and finalize. Session
export without explicit confirmation. Best for local development, personal
analysis, and trusted MCP clients.

```bash
# Explicit (same as default)
export ALEPH_CONTEXT_POLICY=trusted
```

### Isolated

Explicit-consent mode. Designed for shared-server deployments, untrusted
MCP clients, or any scenario where context should not leave RAM without
deliberate action.

```bash
export ALEPH_CONTEXT_POLICY=isolated
```

Behavioral differences in isolated mode:

| Surface | Trusted | Isolated |
|---|---|---|
| `get_variable("ctx")` | blocked (always) | blocked with alternatives guidance |
| `save_session` | works without confirm | requires `confirm=true` |
| `load_session` | works without confirm | requires `confirm=true` |
| Auto memory-pack save | on finalize | disabled |
| Auto memory-pack load | on startup | disabled |

When a tool is blocked in isolated mode, the response includes actionable
alternatives (e.g. use `exec_python` + `get_variable`, `peek_context`,
`search_context`) and a hint to switch policy via `configure()` if
appropriate.

### Switching at Runtime

```python
configure(context_policy="isolated")   # Enables isolated guards
configure(context_policy="trusted")    # Restores defaults
```

The `configure` response confirms the new policy and explains what changed.

---

## Output Feedback Mode

Controls how `exec_python` results are formatted in the core RLM loop.
Aligned with the RLM paper's observation that metadata-only feedback can
reduce context window consumption.

| Mode | Behavior |
|---|---|
| `full` (default) | Raw stdout, stderr, return value, and error in the prompt |
| `metadata` | Structured summary: status, line/char counts, return type, variable names, execution time. Raw content omitted. |

```bash
export ALEPH_OUTPUT_FEEDBACK=metadata
```

Or at runtime:

```python
configure(output_feedback="metadata")
```

**When to use `metadata` mode:** Large-scale analysis where `exec_python`
produces verbose output that would fill the context window. The LLM sees
dimensions (e.g. "stdout_lines: 42, stdout_chars: 8301") and can request
specific slices via `peek_context` or `get_variable` as needed.

**When to keep `full` mode:** Interactive exploration, debugging, and any
workflow where seeing raw output is more efficient than navigating by
metadata.

---

## Context Isolation

Aleph enforces hard boundaries so raw context never leaks into MCP tool
responses:

| Boundary | Default | Env Variable |
|---|---|---|
| MCP tool response cap | 10,000 chars | `ALEPH_MAX_TOOL_RESPONSE_CHARS` |
| Sandbox output cap | 50,000 chars | (via `SandboxConfig.max_output_chars`) |
| `get_variable("ctx")` | blocked | N/A |
| System prompt preview | omitted | N/A |

**`get_variable("ctx")` is blocked.** The MCP boundary refuses to return
the raw context variable. In isolated mode the error message lists
alternatives. Process data inside `exec_python` and retrieve only compact
derived results:

```python
exec_python(code="summary = ctx[:100]", context_id="doc")
get_variable(name="summary", context_id="doc")
```

**Execution output is truncated.** `exec_python` stdout, stderr, and
return values are each truncated at the sandbox level, then the formatted
MCP response is capped again at `max_tool_response_chars`.

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
   which claude codex gemini

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

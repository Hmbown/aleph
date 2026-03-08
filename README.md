# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

Aleph is an [MCP server](https://modelcontextprotocol.io/) plus companion skill
workflow (`/aleph` in Claude Code, `$aleph` in Codex CLI) for recursive LLM work.
It stores working data in a Python process and exposes tools so the model can
retrieve slices, run code, and iterate without repeatedly injecting full files
into prompt context.

Core capabilities:

- Load large files and codebases into process memory
- Search and inspect targeted ranges (`search_context`, `peek_context`)
- Run computation over context with `exec_python`
- Orchestrate recursive sub-queries and recipe pipelines
- Save and restore sessions for long investigations

Design is based on the
[Recursive Language Model](https://arxiv.org/abs/2512.24601) (RLM) architecture.

```text
+-----------------+    tool calls     +--------------------------+
|   LLM client    | ---------------> |  Aleph (Python process)  |
| (context budget)| <--------------- |  search / peek / exec    |
+-----------------+   small results  +--------------------------+
```

## Quick Start

1. Install:

```bash
pip install "aleph-rlm[mcp]"
```

2. Auto-configure your MCP client:

```bash
aleph-rlm install
```

`aleph-rlm install` / `aleph-rlm configure` now treats Codex as the default
sub-query path. When the `codex` CLI is installed, generated configs pin the
Codex MCP defaults (`backend=codex`, `mode=mcp`, `model=gpt-5.4`,
`reasoning=low`, `share_session=true`) unless you override them. Other CLI
backends remain available as explicit experimental overrides.

Recommended setup:

- Best default: install Codex CLI, then run `aleph-rlm install`. This is the
  simplest and strongest path even if your top-level client is Claude Code.
  Aleph can keep Claude as the outer client and still use Codex for nested
  sub-queries.
- All-Claude alternative: if you want Claude to handle nested sub-queries too,
  explicitly set `ALEPH_SUB_QUERY_BACKEND=claude` and
  `ALEPH_SUB_QUERY_SHARE_SESSION=true`, or pick `claude` in
  `aleph-rlm configure`.

3. Verify Aleph is reachable in your assistant:

```text
get_status()
# or
list_contexts()
```

4. Run the skill flow on a real file:

```bash
/aleph path/to/large_file.log
# or in Codex CLI
$aleph path/to/large_file.log
```

Expected behavior: Aleph loads the file into process memory, then begins
analysis with tool calls (`search_context`, `peek_context`, `exec_python`)
without requesting pasted raw content.

### Two-Minute Smoke Test

Use a file inside your current Aleph workspace and run:

```text
get_status()
load_file(path="/absolute/path/to/file", context_id="smoke")
search_context(pattern="TODO|ERROR|WARN", context_id="smoke")
exec_python(code="result = {'lines': line_count(), 'headings': len(search(r'^## '))}", context_id="smoke")
get_variable(name="result", context_id="smoke")
save_session(context_id="smoke", path=".aleph/smoke_session.json")
```

Healthy behavior:

1. `get_status()` reports the expected `workspace_root`
2. `load_file(...)` creates the context without pasting raw file content
3. `search_context`, `exec_python`, and `get_variable` return compact derived results
4. `save_session(...)` succeeds when the path stays under the workspace root

If you try to save outside the workspace root, Aleph rejects the path instead
of silently writing elsewhere.

## Common Workloads

| Scenario | What Aleph Does |
|---|---|
| Large log analysis | Load large logs, trace patterns, correlate events |
| Codebase navigation | Search symbols, inspect routes, trace behavior |
| Data exploration | Analyze JSON/CSV exports with Python helpers |
| Mixed document ingestion | Load PDFs, Word docs, HTML, and compressed logs |
| Semantic retrieval | Use semantic search, then zoom with line/char peeks |
| Long investigations | Save sessions and resume from memory packs |

## Commands

Installing `aleph-rlm` gives you three commands:

| Command | Purpose |
|---|---|
| `aleph` | MCP server (also supports `run` / `shell`) |
| `aleph-rlm` | Installer/config helper (also supports `run` / `shell`) |
| `alef` | Legacy standalone CLI (deprecated) |

How to think about it:

- Run `aleph-rlm install` once to configure clients.
- MCP clients should run `aleph` as the server command.
- Use `aleph run` (or `aleph-rlm run`) for terminal-only mode.

## MCP Mode

### Automatic Setup

```bash
aleph-rlm install
```

To customize workspace scope, backend, docs mode, or Docker settings:

```bash
aleph-rlm configure
```

### Manual Setup (Any MCP Client)

Use this as a practical default:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise"]
    }
  }
}
```

### Verify MCP Wiring

In your assistant session:

```text
get_status()
```

If your client namespaces tools, use `mcp__aleph__get_status`.

### Config File Locations

| Client | macOS/Linux | Windows |
|---|---|---|
| Claude Code | `~/.claude/settings.json` | `%USERPROFILE%\.claude\settings.json` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` | `%USERPROFILE%\.cursor\mcp.json` |
| VS Code | `~/.vscode/mcp.json` | `%USERPROFILE%\.vscode\mcp.json` |
| Codex CLI | `~/.codex/config.toml` | `%USERPROFILE%\.codex\config.toml` |

More per-client setup details are in [MCP_SETUP.md](MCP_SETUP.md).

## The `/aleph` and `$aleph` Skill

For skill-based usage, configure both:

1. MCP server configured in the client
2. Skill prompt installed (`docs/prompts/aleph.md`)

### Invocation

| Client | Skill command | Typical usage |
|---|---|---|
| Claude Code | `/aleph` | `/aleph path/to/file` |
| Codex CLI | `$aleph` | `$aleph path/to/file` |

### Skill Install Paths

Download [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and place it at:

- Claude Code: `~/.claude/commands/aleph.md`
- Codex CLI: `~/.codex/skills/aleph/SKILL.md`

Windows equivalents:

- `%USERPROFILE%\.claude\commands\aleph.md`
- `%USERPROFILE%\.codex\skills\aleph\SKILL.md`

### Quick Behavior Check

Use this exact prompt:

```text
$aleph path/to/large_file.log
Then call list_contexts() and show the loaded context_id before analysis.
```

Healthy behavior:

1. Tool call to `load_file(path=...)`
2. Context appears in `list_contexts()`
3. Follow-up search/peek/exec on that context

### Backend Selection Rules

Aleph resolves the active sub-query backend in this order:

1. Programmatic config via `configure(sub_query_backend=...)` or `SubQueryConfig(backend=...)`
2. `ALEPH_SUB_QUERY_BACKEND` when it is set to a concrete backend
3. Auto-detection: `codex` -> `api`

That means an explicit runtime switch wins over ambient shell state, while
`auto` keeps the first-class Codex path and otherwise falls back to API.

### Shared-Session Architecture

When `ALEPH_SUB_QUERY_SHARE_SESSION=true`, Aleph starts a local streamable HTTP
server and points the sub-query CLI back at that live Aleph session. That is
what lets nested sub-agents use `search_context`, `peek_context`,
`exec_python`, and the rest of Aleph's MCP tools instead of relying on a pasted
prompt slice.

How Aleph injects that live MCP server depends on the backend:

| Backend | How shared-session MCP is injected |
|---|---|
| `codex` | Native Codex MCP config overrides via `codex mcp-server` |
| `claude` | Temp JSON file via `--mcp-config` and `--strict-mcp-config` |
| `gemini` | Temp JSON file via `GEMINI_CLI_SYSTEM_SETTINGS_PATH` |

This is why Codex is the recommended default: it has the cleanest nested MCP
path, the least ambient config leakage, and the best retry/thread behavior.

## Core Workflow Patterns

### 1) Load File -> Work Immediately

```python
load_file(path="/absolute/path/to/large_file.log", context_id="doc")
search_context(pattern="ERROR|WARN", context_id="doc")
peek_context(start=1, end=60, unit="lines", context_id="doc")
exec_python(code="print(line_count())", context_id="doc")
finalize(answer="Summary...", context_id="doc")
```

Note: with MCP action tools, absolute paths are safest for `load_file`.

### 2) Analyze Raw Text

```python
load_context(content=data_text, context_id="doc")
search_context(pattern="keyword", context_id="doc")
finalize(answer="Found X at line Y", context_id="doc")
```

### 3) Recipe Pipelines

Recommended sequence:

```text
validate_recipe -> estimate_recipe -> run_recipe
```

Example:

```python
run_recipe(recipe={
  "version": "aleph.recipe.v1",
  "context_id": "doc",
  "budget": {"max_steps": 6, "max_sub_queries": 5},
  "steps": [
    {"op": "search", "pattern": "ERROR|WARN", "max_results": 10},
    {"op": "map_sub_query", "prompt": "Root cause?", "context_field": "context"},
    {"op": "aggregate", "prompt": "Top causes with evidence"},
    {"op": "finalize"}
  ]
})
```

### 4) Sub-Query Batching (Important)

Prefer fewer large sub-query calls over many tiny calls.

- Bad: 1000 calls of 1K chars
- Good: 5-10 calls of about 100K to 200K chars

```python
exec_python(code="""
chunks = chunk(100000)
summaries = sub_query_batch("Summarize this chunk:", chunks)
print(summaries)
""", context_id="doc")
```

### 5) Save and Resume

```python
save_session(context_id="doc", path=".aleph/session_doc.json")
load_session(path=".aleph/session_doc.json")
```

Paths for `save_session()` / `load_session()` must stay inside Aleph's
workspace root. `.aleph/` under the repo is a safe default.

## CLI Mode (Standalone)

Use this when you want Aleph without MCP integration.

```bash
# Basic
aleph run "What is 2+2?" --provider cli --model codex

# With file context
aleph run "Summarize this log" --provider cli --model codex --context-file app.log

# JSON output with trajectory
aleph run "Analyze" --provider cli --model codex --context-file data.json --json --include-trajectory
```

### Common Flags

| Flag | Description |
|---|---|
| `--provider cli` | Use local CLI tools instead of API provider |
| `--model codex|claude|gemini|kimi` | CLI backend to use (`codex` recommended; others are explicit experimental overrides) |
| `--context-file <path>` | Load context from file |
| `--context-stdin` | Read context from stdin |
| `--json` | Emit JSON output |
| `--include-trajectory` | Include full reasoning trace |
| `--max-iterations N` | Limit loop steps |

### Common Environment Variables

| Variable | Description |
|---|---|
| `ALEPH_SUB_QUERY_BACKEND` | `auto`, `codex`, `gemini`, `kimi`, `claude`, or `api` |
| `ALEPH_SUB_QUERY_TIMEOUT` | Sub-query timeout in seconds |
| `ALEPH_SUB_QUERY_SHARE_SESSION` | Share MCP session with CLI sub-agents |
| `ALEPH_SUB_QUERY_CODEX_MODE` | Codex backend mode: defaults to `mcp` |
| `ALEPH_SUB_QUERY_CODEX_MODEL` | Codex MCP model override, default `gpt-5.4` |
| `ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT` | Codex MCP reasoning effort, default `low` |
| `ALEPH_SUB_QUERY_GEMINI_SANDBOX` | Opt back into Gemini CLI sandboxing (`false` by default for Aleph sub-queries) |
| `ALEPH_CLI_TIMEOUT` | Timeout for CLI calls |

When Aleph resolves to the `codex` backend, it now defaults to the internal
`codex mcp-server` path with a clean MCP graph (`-c mcp_servers={}`).

`gemini`, `claude`, and `kimi` remain available only when you explicitly pin
them via `ALEPH_SUB_QUERY_BACKEND` or runtime `configure(...)`.

In live dogfooding, `claude` and `gemini` both worked as shared-session Aleph
MCP backends after explicit selection, but Codex was more reliable for
exact-output and retry-sensitive tasks.

Aleph launches Gemini sub-queries with `--extensions ""` so the nested run does
not inherit unrelated user extensions from `~/.gemini`.

If you want the simplest Codex-backed shared-session setup:

```bash
aleph-rlm install
```

If you want an all-Claude setup instead:

```bash
export ALEPH_SUB_QUERY_BACKEND=claude
export ALEPH_SUB_QUERY_SHARE_SESSION=true
```

If Codex is installed, `aleph-rlm install` will still pin Codex by default
until you override it.

Use these env vars if you want to pin the Codex defaults explicitly:

```bash
export ALEPH_SUB_QUERY_BACKEND=codex
export ALEPH_SUB_QUERY_CODEX_MODE=mcp
export ALEPH_SUB_QUERY_CODEX_MODEL=gpt-5.4
export ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT=low
export ALEPH_SUB_QUERY_SHARE_SESSION=true
```

Quick smoke for the nested Codex MCP path:

```text
load_context(content="Fact: nested codex mcp smoke works", context_id="smoke")
exec_python(code="result = sub_query(\"Use Aleph MCP tools to inspect context_id='smoke'. Return exactly the fact text and nothing else. Do not rely on embedded context.\")", context_id="smoke")
get_variable(name="result", context_id="smoke")
```

If `get_variable(...)` returns `nested codex mcp smoke works` exactly, Aleph is
sharing the live MCP session with the nested Codex agent and the nested agent
is reading through Aleph tools instead of prompt-stuffed context.

## Tool Overview

### Core Tools (Always Available)

| Category | Tools |
|---|---|
| Context | `load_context`, `list_contexts`, `diff_contexts` |
| Search | `search_context`, `semantic_search`, `peek_context`, `chunk_context` |
| Compute | `exec_python`, `get_variable` |
| Reasoning | `think`, `evaluate_progress`, `summarize_so_far`, `get_evidence`, `finalize` |
| Runtime Config | `configure` |
| Recipes | `validate_recipe`, `estimate_recipe`, `run_recipe`, `compile_recipe`, `run_recipe_code` |

### Action Tools (`--enable-actions`)

| Category | Tools |
|---|---|
| Filesystem | `load_file`, `read_file`, `write_file` |
| Shell | `run_command`, `run_tests`, `rg_search` |
| Persistence | `save_session`, `load_session` |
| Remote MCP | `add_remote_server`, `list_remote_servers`, `list_remote_tools`, `call_remote_tool`, `close_remote_server` |

`exec_python` includes 100+ helpers (`search`, `chunk`, `lines`, `extract_*`,
`sub_query`, `sub_query_batch`, `sub_query_map`, `sub_aleph`, Recipe DSL helpers,
and more). Recursion helpers are available inside `exec_python`, not as top-level
MCP tools.

## Swarm Mode (Optional)

Aleph can act as shared memory for multiple agents.

```text
Agent A/B/C <-> Aleph contexts in shared RAM
```

Simple pattern:

1. Shared KB context: `swarm-<name>-kb`
2. Task contexts: `task-<id>-spec`, `task-<id>-findings`
3. Agent-private contexts: `<agent>-workspace`

Example write/read:

```python
exec_python(code="ctx_append('Auth uses JWT with RS256')", context_id="task-42-findings")
search_context(pattern="JWT", context_id="task-42-findings")
```

## Context Isolation and Safety

Aleph enforces strict boundaries to prevent raw context from leaking into
the LLM's context window:

- **System prompt isolation.** The default system prompt does not include a
  raw context preview. The placeholder is replaced with
  `[OMITTED FOR CONTEXT ISOLATION]`.
- **`get_variable("ctx")` is policy-aware.** In `isolated` policy, retrieving
  `ctx` via the MCP boundary is blocked with guidance. In `trusted` policy, it
  is allowed but still subject to response caps/truncation. Prefer processing
  data inside `exec_python` and retrieving compact derived results with
  `get_variable`.
- **Execution output truncation.** `exec_python` stdout, stderr, and return
  values are all truncated to `max_output_chars` (default 50,000). The MCP
  tool response is further capped at `max_tool_response_chars` (default
  10,000). Both limits are configurable.
- **Tool response caps.** Every MCP tool response (peek, search, semantic
  search, get_variable, etc.) is bounded by the same response-size cap.

### Deployment Profiles

Set `ALEPH_CONTEXT_POLICY` to choose a profile:

| Profile | Behavior |
|---|---|
| `trusted` (default) | Low friction. Auto memory-pack, session save/load without confirmation. |
| `isolated` | Explicit consent. Requires `confirm=true` for session export/import, disables auto memory-pack. Blocked tools return actionable alternatives. |

Switch at runtime with `configure(context_policy="isolated")`. See
[CONFIGURATION.md](docs/CONFIGURATION.md#deployment-profiles) for details.

### Safe Usage Pattern

```python
# Compute server-side — data stays in Aleph RAM
exec_python(code="""
errors = [l for l in ctx.splitlines() if 'error' in l.lower()]
result = f'Found {len(errors)} errors. First 3: {errors[:3]}'
""", context_id="doc")

# Retrieve only the small derived result
get_variable(name="result", context_id="doc")
```

Avoid returning full-context payloads unless necessary. In `isolated` policy,
`get_variable("ctx")` is blocked; in `trusted` policy large raw responses are
still truncated by output caps.

## Configuration Quick Reference

### Workspace and Safety

| Flag/Variable | Purpose |
|---|---|
| `--workspace-root <path>` | Root for relative action paths |
| `--workspace-mode <fixed|git|any>` | Path access policy |
| `--require-confirmation` | Require `confirm=true` for actions |
| `ALEPH_WORKSPACE_ROOT` | Override workspace root |
| `ALEPH_CONTEXT_POLICY` | `trusted` (default) or `isolated` |
| `ALEPH_OUTPUT_FEEDBACK` | `full` (default) or `metadata` |
| `ALEPH_MAX_RECIPE_CONCURRENCY` | Max parallel `map_sub_query` tasks (default `10`) |

### Limits

| Flag | Default | Purpose |
|---|---|---|
| `--max-file-size` | 1 GB | Max file read size |
| `--max-write-bytes` | 100 MB | Max file write size |
| `--timeout` | 180 s | Sandbox/command timeout |
| `--max-output` | 50,000 chars | Max command output |
| `ALEPH_MAX_TOOL_RESPONSE_CHARS` | 10,000 chars | MCP tool response cap |

### Recursion Budgets

| Variable | Default | Purpose |
|---|---|---|
| `ALEPH_MAX_DEPTH` | 2 | Max `sub_aleph` nesting depth |
| `ALEPH_MAX_ITERATIONS` | 100 | Total RLM steps |
| `ALEPH_MAX_WALL_TIME` | 300 s | Wall-time cap |
| `ALEPH_MAX_SUB_QUERIES` | 100 | Max `sub_query` calls |
| `ALEPH_MAX_TOKENS` | unset | Optional per-call output cap |

Full configuration details: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

## Troubleshooting

- Tool not found: ensure Aleph MCP server is running.
- Context not found: verify `context_id` and check `list_contexts()`.
- No search hits: broaden regex or use `semantic_search`.
- `rg_search` is slow: install ripgrep (`rg`).
- Running out of context: use `summarize_so_far()`.
- Session load errors: check file path and memory pack schema.

## Documentation

| Document | Purpose |
|---|---|
| [MCP_SETUP.md](MCP_SETUP.md) | Client-by-client MCP configuration |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Full flags and environment variables |
| [docs/langgraph-rlm-default.md](docs/langgraph-rlm-default.md) | LangGraph integration with RLM-default tool usage |
| [examples/langgraph_rlm_repo_improver.py](examples/langgraph_rlm_repo_improver.py) | Repo-improvement runner with optional LangSmith tracing |
| [docs/prompts/aleph.md](docs/prompts/aleph.md) | Skill workflow and tool reference |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Contributor guide |

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e ".[dev,mcp]"
pytest tests/ -v
ruff check aleph/ tests/
```

## References

- Zhang, A. L., Kraska, T., Khattab, O. (2025)
  [Recursive Language Models (arXiv:2512.24601)](https://arxiv.org/abs/2512.24601)

## License

MIT

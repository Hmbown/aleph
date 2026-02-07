# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

**Your RAM is the new context window.**

Aleph is an [MCP server](https://modelcontextprotocol.io/) for recursive LLM workflows.
Instead of forcing large files into prompt context, Aleph keeps data in a Python
process and gives the model tools to inspect and reason over it.

- Load big files and codebases into external memory
- Search, slice, and compute with `exec_python`
- Run recursive sub-queries and recipe pipelines
- Save sessions and resume later

Based on the [Recursive Language Model](https://arxiv.org/abs/2512.24601) (RLM)
architecture.

```text
+-----------------+    tool calls     +--------------------------+
|   LLM client    | ---------------> |  Aleph (Python, RAM)     |
|  (limited ctx)  | <--------------- |  search / peek / exec    |
+-----------------+   small results  +--------------------------+
```

## Start Here (2 Minutes)

1. Install:

```bash
pip install "aleph-rlm[mcp]"
```

2. Auto-configure your MCP client:

```bash
aleph-rlm install
```

3. Verify Aleph is reachable in your assistant:

```text
get_status()
# or
list_contexts()
```

4. Use the skill flow on a real file:

```bash
/aleph path/to/large_file.log
# or in Codex CLI
$aleph path/to/large_file.log
```

Expected behavior: Aleph loads the file into memory and immediately begins
analysis (search/peek/exec), without asking you to paste raw file content.

## Use Cases

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

Mental model:

- Run `aleph-rlm install` once to configure clients.
- MCP clients should run `aleph` as the server command.
- Use `aleph run` (or `aleph-rlm run`) for terminal-only mode.

## MCP Mode (Recommended)

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

Aleph works best with both:

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

## CLI Mode (Standalone)

Use this when you want Aleph without MCP integration.

```bash
# Basic
aleph run "What is 2+2?" --provider cli --model claude

# With file context
aleph run "Summarize this log" --provider cli --model claude --context-file app.log

# JSON output with trajectory
aleph run "Analyze" --provider cli --model claude --context-file data.json --json --include-trajectory
```

### Common Flags

| Flag | Description |
|---|---|
| `--provider cli` | Use local CLI tools instead of API provider |
| `--model claude|codex|gemini` | CLI backend to use |
| `--context-file <path>` | Load context from file |
| `--context-stdin` | Read context from stdin |
| `--json` | Emit JSON output |
| `--include-trajectory` | Include full reasoning trace |
| `--max-iterations N` | Limit loop steps |

### Common Environment Variables

| Variable | Description |
|---|---|
| `ALEPH_SUB_QUERY_BACKEND` | `auto`, `codex`, `gemini`, `claude`, or `api` |
| `ALEPH_SUB_QUERY_TIMEOUT` | Sub-query timeout in seconds |
| `ALEPH_SUB_QUERY_SHARE_SESSION` | Share MCP session with CLI sub-agents |
| `ALEPH_CLI_TIMEOUT` | Timeout for CLI calls |

## Tool Overview

### Core Tools (Always Available)

| Category | Tools |
|---|---|
| Context | `load_context`, `list_contexts`, `diff_contexts` |
| Search | `search_context`, `semantic_search`, `peek_context`, `chunk_context` |
| Compute | `exec_python`, `get_variable` |
| Reasoning | `think`, `evaluate_progress`, `summarize_so_far`, `get_evidence`, `finalize` |
| Recursion | `sub_query`, `sub_aleph` |
| Recipes | `validate_recipe`, `estimate_recipe`, `run_recipe`, `compile_recipe`, `run_recipe_code` |

### Action Tools (`--enable-actions`)

| Category | Tools |
|---|---|
| Filesystem | `load_file`, `read_file`, `write_file` |
| Shell | `run_command`, `run_tests`, `rg_search` |
| Persistence | `save_session`, `load_session` |
| Remote MCP | `add_remote_server`, `list_remote_tools`, `call_remote_tool`, `close_remote_server` |

`exec_python` includes 100+ helpers (`search`, `chunk`, `lines`, `extract_*`,
`sub_query_batch`, Recipe DSL helpers, and more).

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

## Configuration Quick Reference

### Workspace and Safety

| Flag/Variable | Purpose |
|---|---|
| `--workspace-root <path>` | Root for relative action paths |
| `--workspace-mode <fixed|git|any>` | Path access policy |
| `--require-confirmation` | Require `confirm=true` for actions |
| `ALEPH_WORKSPACE_ROOT` | Override workspace root |

### Limits

| Flag | Default | Purpose |
|---|---|---|
| `--max-file-size` | 1 GB | Max file read size |
| `--max-write-bytes` | 100 MB | Max file write size |
| `--timeout` | 60 s | Sandbox/command timeout |
| `--max-output` | 50,000 chars | Max command output |

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

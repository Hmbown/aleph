# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

**Your RAM is the new context window.**

Aleph is an [MCP server](https://modelcontextprotocol.io/) that gives any LLM access to gigabytes of local data without consuming context. Load massive files into a Python process—the model explores them via search, slicing, and sandboxed code execution. Only results enter the context window, never the raw content.

Based on the [Recursive Language Model](https://arxiv.org/abs/2512.24601) (RLM) architecture.

## Use Cases

| Scenario | What Aleph Does |
|----------|-----------------|
| **Large log analysis** | Load 500MB of logs, search for patterns, correlate across time ranges |
| **Codebase navigation** | Load entire repos, find definitions, trace call chains, extract architecture |
| **Data exploration** | JSON exports, CSV files, API responses—explore interactively with Python |
| **Mixed document ingestion** | Load PDFs, Word docs, HTML, and logs like plain text |
| **Semantic search** | Find relevant sections by meaning, then zoom in with peek |
| **Research sessions** | Save/resume sessions, track evidence with citations, spawn sub-queries |

## Requirements

- Python 3.10+
- An MCP-compatible client: [Claude Code](https://claude.ai/code), [Cursor](https://cursor.sh), [VS Code](https://code.visualstudio.com/), [Windsurf](https://codeium.com/windsurf), [Codex CLI](https://github.com/openai/codex), or [Claude Desktop](https://claude.ai/download)

## Quickstart

### 1. Install

```bash
pip install "aleph-rlm[mcp]"
```

### 2. Configure your MCP client

**Automatic** (recommended):
```bash
aleph-rlm install
```

This auto-detects your installed clients and configures them.

**Manual** (any MCP client):
```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions", "--workspace-mode", "any"]
    }
  }
}
```

<details>
<summary><strong>Config file locations</strong></summary>

| Client | macOS/Linux | Windows |
|--------|-------------|---------|
| Claude Code | `~/.claude/settings.json` | `%USERPROFILE%\.claude\settings.json` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` | `%USERPROFILE%\.cursor\mcp.json` |
| VS Code | `~/.vscode/mcp.json` | `%USERPROFILE%\.vscode\mcp.json` |
| Codex CLI | `~/.codex/config.toml` | `%USERPROFILE%\.codex\config.toml` |

</details>

See [MCP_SETUP.md](MCP_SETUP.md) for detailed instructions.

### 3. Verify

In your assistant, run:
```
get_status()
```

If using Claude Code, tools are prefixed: `mcp__aleph__get_status`.

## AI Assistant Setup (MCP + `/aleph` Skill) — Copy/Paste

Paste this into any AI coding assistant or agentic coder to add Aleph (MCP server + `/aleph` skill):

```
You are an AI coding assistant. Please set up Aleph (Model Context Protocol / MCP).

1) Add the Aleph MCP server config:
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions", "--workspace-mode", "any"]
    }
  }
}

2) Install the /aleph skill prompt:
- Claude Code: copy docs/prompts/aleph.md -> ~/.claude/commands/aleph.md
- Codex CLI: copy docs/prompts/aleph.md -> ~/.codex/skills/aleph/SKILL.md
- Gemini CLI: copy docs/prompts/aleph.md -> ~/.gemini/skills/aleph/SKILL.md
  Ensure ~/.gemini/settings.json has "experimental": { "skills": true } and restart.
If this client uses a different skill/command folder, ask me where to place it.

3) Verify: run get_status() or list_contexts().
If tools are namespaced, use mcp__aleph__get_status or mcp__aleph__list_contexts.

4) (Optional) Enable sub_query (recursive sub-agent):
- CLI backend (no API key): set ALEPH_SUB_QUERY_BACKEND=claude|codex|gemini
- API backend: set ALEPH_SUB_QUERY_API_KEY + ALEPH_SUB_QUERY_MODEL (+ optional ALEPH_SUB_QUERY_URL)
If env vars can't be set in the MCP config, add them to your shell profile and restart.

5) Use the skill: /aleph (Claude Code) or $aleph (Codex CLI).
Gemini CLI: /skills list (use /skills enable aleph if disabled).
```

## The `/aleph` Skill

The `/aleph` skill is a prompt that teaches your LLM how to use Aleph effectively. It provides workflow patterns, tool guidance, and troubleshooting tips.

**Note:** Aleph works best when paired with the skill prompt + MCP server together.

### What it does

- Loads files into searchable in-memory contexts
- Tracks evidence with citations as you reason
- Supports semantic search and fast rg-based codebase search
- Enables recursive sub-queries for deep analysis
- Persists sessions for later resumption (memory packs)

### How to invoke

| Client | Command |
|--------|---------|
| Claude Code | `/aleph` |
| Codex CLI | `$aleph` |

For other clients, copy [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and paste it at session start.

### Installing the skill

**Option 1: Direct download** (simplest)

Download [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and save it to:
- **Claude Code:** `~/.claude/commands/aleph.md` (macOS/Linux) or `%USERPROFILE%\.claude\commands\aleph.md` (Windows)
- **Codex CLI:** `~/.codex/skills/aleph/SKILL.md` (macOS/Linux) or `%USERPROFILE%\.codex\skills\aleph\SKILL.md` (Windows)

**Option 2: From installed package**

<details>
<summary>macOS/Linux</summary>

```bash
# Claude Code
mkdir -p ~/.claude/commands
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" ~/.claude/commands/aleph.md

# Codex CLI
mkdir -p ~/.codex/skills/aleph
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" ~/.codex/skills/aleph/SKILL.md
```
</details>

<details>
<summary>Windows (PowerShell)</summary>

```powershell
# Claude Code
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\commands"
$alephPath = python -c "import aleph; print(aleph.__path__[0])"
Copy-Item "$alephPath\..\docs\prompts\aleph.md" "$env:USERPROFILE\.claude\commands\aleph.md"

# Codex CLI  
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills\aleph"
Copy-Item "$alephPath\..\docs\prompts\aleph.md" "$env:USERPROFILE\.codex\skills\aleph\SKILL.md"
```
</details>

## How It Works

```
┌───────────────┐    tool calls     ┌────────────────────────┐
│   LLM client  │ ────────────────► │  Aleph (Python, RAM)   │
│ (limited ctx) │ ◄──────────────── │  search/peek/exec      │
└───────────────┘    small results  └────────────────────────┘
```

1. **Load** — `load_context` (paste text) or `load_file` (from disk)
2. **Explore** — `search_context`, `semantic_search`, `peek_context`
3. **Compute** — `exec_python` with 100+ built-in helpers
4. **Reason** — `think`, `evaluate_progress`, `get_evidence`
5. **Persist** — `save_session` to resume later

### Quick Example

```python
# Load log data
load_context(content=logs, context_id="logs")
# → "Context loaded 'logs': 445 chars, 7 lines, ~111 tokens"

# Search for errors
search_context(pattern="ERROR", context_id="logs")
# → Found 2 match(es):
#   Line 1: 2026-01-15 10:23:45 ERROR [auth] Failed login...
#   Line 4: 2026-01-15 10:24:15 ERROR [db] Connection timeout...

# Extract structured data
exec_python(code="emails = extract_emails(); print(emails)", context_id="logs")
# → [{'value': 'user@example.com', 'line_num': 0, 'start': 50, 'end': 66}, ...]
```

### Advanced Workflows

**Multi-Context Workflow (code + docs + diffs)**

Load multiple sources, then compare or reconcile them:

```python
# Load a design doc and a repo snapshot (or any two sources)
load_context(content=design_doc_text, context_id="spec")
rg_search(pattern="AuthService|JWT|token", paths=["."], load_context_id="repo_hits", confirm=true)

# Compare or reconcile
diff_contexts(a="spec", b="repo_hits")
search_context(pattern="missing|TODO|mismatch", context_id="repo_hits")
```

**Advanced Querying with `exec_python`**

Treat `exec_python` as a reasoning tool, not just code execution:

```python
# Example: extract class names or key sections programmatically
exec_python(code="print(extract_classes())", context_id="repo_hits")
```

## Tools

**Core** (always available):
- `load_context`, `list_contexts`, `diff_contexts` — manage in-memory data
- `search_context`, `semantic_search`, `peek_context`, `chunk_context` — explore data; use `semantic_search` for concepts/fuzzy queries, `search_context` for precise regex
- `exec_python`, `get_variable` — compute in sandbox (100+ built-in helpers)
- `think`, `evaluate_progress`, `summarize_so_far`, `get_evidence`, `finalize` — structured reasoning
- `tasks` — lightweight task tracking per context
- `get_status` — session state
- `sub_query` — spawn recursive sub-agents (CLI or API backend)

<details>
<summary><strong>exec_python helpers</strong></summary>

The sandbox includes 100+ helpers that operate on the loaded context:

| Category | Examples |
|----------|----------|
| **Extractors** (25) | `extract_emails()`, `extract_urls()`, `extract_dates()`, `extract_ips()`, `extract_functions()` |
| **Statistics** (8) | `word_count()`, `line_count()`, `word_frequency()`, `ngrams()` |
| **Line operations** (12) | `head()`, `tail()`, `grep()`, `sort_lines()`, `columns()` |
| **Text manipulation** (15) | `replace_all()`, `between()`, `truncate()`, `slugify()` |
| **Validation** (7) | `is_email()`, `is_url()`, `is_json()`, `is_numeric()` |
| **Core** | `peek()`, `lines()`, `search()`, `chunk()`, `cite()` |

Extractors return `list[dict]` with keys: `value`, `line_num`, `start`, `end`.

</details>

**Action tools** (requires `--enable-actions`):
- `load_file`, `read_file`, `write_file` — filesystem (PDFs, Word, HTML, .gz supported)
- `run_command`, `run_tests`, `rg_search` — shell + fast repo search
- `save_session`, `load_session` — persist state (memory packs)
- `add_remote_server`, `list_remote_tools`, `call_remote_tool` — MCP orchestration

## Configuration

**Workspace controls:**
- `ALEPH_WORKSPACE_ROOT` — environment variable to override workspace detection
- `--workspace-root <path>` — root for relative paths (default: auto-detect from env/cwd)
- `--workspace-mode <fixed|git|any>` — path restrictions
- `--require-confirmation` — require `confirm=true` on action calls

**Limits:**
- `--max-file-size` — max file read (default: 1GB)
- `--max-write-bytes` — max file write (default: 100MB)  
- `--timeout` — sandbox/command timeout (default: 60s)
- `--max-output` — max command output (default: 50,000 chars)

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all options.

## Documentation

- [MCP_SETUP.md](MCP_SETUP.md) — client configuration
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — CLI flags and environment variables
- [docs/prompts/aleph.md](docs/prompts/aleph.md) — skill prompt and tool reference
- [CHANGELOG.md](CHANGELOG.md) — release history
- [DEVELOPMENT.md](DEVELOPMENT.md) — contributing guide

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e ".[dev,mcp]"
pytest
```

## References

> **Recursive Language Models**  
> Zhang, A. L., Kraska, T., & Khattab, O. (2025)  
> [arXiv:2512.24601](https://arxiv.org/abs/2512.24601)

## License

MIT

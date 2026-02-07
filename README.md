# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

**Your RAM is the new context window.**

Aleph is an [MCP server](https://modelcontextprotocol.io/) that gives any LLM
access to gigabytes of local data without consuming context. Load massive files
into a Python process -- the model explores them via search, slicing, and
sandboxed code execution. Only results enter the context window, never the raw
content.

Based on the [Recursive Language Model](https://arxiv.org/abs/2512.24601) (RLM)
architecture.

```
+-----------------+    tool calls     +--------------------------+
|   LLM client    | ---------------> |  Aleph (Python, RAM)     |
|  (limited ctx)  | <--------------- |  search / peek / exec    |
+-----------------+   small results  +--------------------------+
```

---

## Use Cases

| Scenario                      | What Aleph Does                                                       |
|-------------------------------|-----------------------------------------------------------------------|
| **Large log analysis**        | Load 500 MB of logs, search for patterns, correlate across time       |
| **Codebase navigation**       | Load entire repos, find definitions, trace call chains                |
| **Data exploration**          | JSON exports, CSV files, API responses -- explore with Python         |
| **Mixed document ingestion**  | Load PDFs, Word docs, HTML, and logs as plain text                    |
| **Semantic search**           | Find relevant sections by meaning, then zoom in with peek             |
| **Research sessions**         | Save/resume sessions, track evidence with citations, spawn sub-queries|

---

## Requirements

- Python 3.10+
- **MCP mode:** an MCP-compatible client
  ([Claude Code](https://claude.ai/code),
  [Cursor](https://cursor.sh),
  [VS Code](https://code.visualstudio.com/),
  [Windsurf](https://codeium.com/windsurf),
  [Codex CLI](https://github.com/openai/codex), or
  [Claude Desktop](https://claude.ai/download))
- **CLI mode:** `claude`, `codex`, or `gemini` CLI installed

---

## Quickstart

### 1. Install

```bash
pip install "aleph-rlm[mcp]"
```

This installs three commands:

| Command      | Purpose                                                                     |
|--------------|-----------------------------------------------------------------------------|
| `aleph`      | MCP server -- connect from any MCP client (also supports `run` / `shell`)   |
| `aleph-rlm`  | Setup utility -- auto-configure MCP clients (also supports `run` / `shell`) |
| `alef`       | Standalone CLI -- **deprecated** (use `aleph run` or `aleph-rlm run`)       |

Quick mental model:

- Use **`aleph-rlm`** once to configure MCP clients.
- Your MCP client runs **`aleph`** as the server command.
- Use **`aleph run`** or **`aleph-rlm run`** for standalone CLI mode (replaces `alef`).

### 2. Choose Your Mode

**Option A -- MCP mode** (recommended for AI assistants)

Configure your MCP client to use the `aleph` server, then interact via tool
calls.

**Option B -- CLI mode** (standalone terminal use)

Run `aleph run` (or `aleph-rlm run`) directly from the command line -- no MCP
setup required. (`alef` still works for now but is deprecated.)

---

## MCP Mode Setup

### Configure Your MCP Client

**Automatic** (recommended):

```bash
aleph-rlm install
```

This auto-detects your installed clients and configures them with sensible
defaults.

To customize server settings (workspace scope, sub-query backend, Docker, etc.):

```bash
aleph-rlm configure
```

To confirm which client was configured, open the client config file (table
below) and look for an `aleph` entry. If a client was not detected, install or
update it and re-run `aleph-rlm install`, or use the manual config.

**Manual** (any MCP client):

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

**Docker** (optional):

Build the image once, then use `aleph-rlm configure` and choose the Docker
option:

```bash
docker build -t aleph-rlm:local .
```

<details>
<summary><strong>Config file locations</strong></summary>

| Client          | macOS / Linux                                                            | Windows                                          |
|-----------------|--------------------------------------------------------------------------|--------------------------------------------------|
| Claude Code     | `~/.claude/settings.json`                                                | `%USERPROFILE%\.claude\settings.json`             |
| Claude Desktop  | `~/Library/Application Support/Claude/claude_desktop_config.json`        | `%APPDATA%\Claude\claude_desktop_config.json`     |
| Cursor          | `~/.cursor/mcp.json`                                                     | `%USERPROFILE%\.cursor\mcp.json`                  |
| VS Code         | `~/.vscode/mcp.json`                                                     | `%USERPROFILE%\.vscode\mcp.json`                  |
| Codex CLI       | `~/.codex/config.toml`                                                   | `%USERPROFILE%\.codex\config.toml`                |

</details>

See [MCP_SETUP.md](MCP_SETUP.md) for detailed per-client instructions.

### Verify

In your assistant, run:

```
get_status()
```

If using Claude Code, tools are prefixed: `mcp__aleph__get_status`.

---

## CLI Mode

The `aleph run` command runs the full RLM reasoning loop directly from your
terminal. It uses local CLI tools (`claude`, `codex`, or `gemini`) as the LLM
backend -- no separate API keys needed, just the CLI tool's own authentication.
(`aleph-rlm run` works the same way.)

**Prerequisites:** have `claude`, `codex`, or `gemini` CLI installed and
authenticated.

### Basic Usage

```bash
# Simple query
aleph run "What is 2+2?" --provider cli --model claude

# With context from a file
aleph run "Summarize this log" --provider cli --model claude --context-file app.log

# JSON context
aleph run "Extract all names" --provider cli --model claude \
  --context '{"users": [{"name": "Alice"}, {"name": "Bob"}]}'

# Full JSON output with trajectory
aleph run "Analyze this data" --provider cli --model claude \
  --context-file data.json --json --include-trajectory
```

### Sub-Queries (Multi-Claude Recursion)

Enable recursive sub-queries where the LLM spawns additional Claude calls:

```bash
# Enable Claude CLI for sub-queries
export ALEPH_SUB_QUERY_BACKEND=claude

# Run a complex analysis that uses sub_query()
aleph run "For each item, use sub_query to summarize it, then combine results" \
  --provider cli --model claude \
  --context '{"items": [{"name": "Alice", "score": 95}, {"name": "Bob", "score": 87}]}' \
  --max-iterations 10
```

The RLM loop will:

1. Execute Python code blocks to explore the context
2. Call `sub_query()` which spawns additional CLI processes
3. Iterate until `FINAL(answer)` is reached

### CLI Options

| Flag                     | Description                             |
|--------------------------|-----------------------------------------|
| `--provider cli`         | Use local CLI tools instead of API      |
| `--model claude\|codex\|gemini` | Which CLI backend to use          |
| `--context "..."`        | Inline context string                   |
| `--context-file path`    | Load context from file                  |
| `--context-stdin`        | Read context from stdin                 |
| `--json`                 | Output JSON response                    |
| `--include-trajectory`   | Include full reasoning trace in JSON    |
| `--max-iterations N`     | Limit RLM loop iterations               |

### Environment Variables

| Variable                          | Description                                                        |
|-----------------------------------|--------------------------------------------------------------------|
| `ALEPH_SUB_QUERY_BACKEND`        | Backend for `sub_query()`: `claude`, `codex`, `gemini`, or `api`   |
| `ALEPH_SUB_QUERY_SHARE_SESSION`  | Share MCP session with sub-agents (set to `1`)                     |
| `ALEPH_CLI_TIMEOUT`              | Timeout for CLI calls (default: 120s)                              |

---

## Swarm Mode

Aleph enables multi-agent coordination through shared contexts. Multiple agents
can read and write to the same context IDs, creating a distributed memory layer
for swarm architectures.

### How It Works

```
+---------------+     +---------------+     +---------------+
|   Agent A     |     |   Agent B     |     |   Agent C     |
|  (Explorer)   |     |  (Analyst)    |     |  (Writer)     |
+-------+-------+     +-------+-------+     +-------+-------+
        |                      |                     |
        +----------------------+---------------------+
                               |
                        +------+------+
                        |    Aleph    |
                        |  Contexts   |
                        | (Shared RAM)|
                        +-------------+
```

Agents coordinate by reading and writing to shared context IDs. No message
passing needed for data -- agents simply load, search, and write to the same
contexts.

### Context Naming Conventions

| Pattern                 | Purpose                    | Example                |
|-------------------------|----------------------------|------------------------|
| `swarm-{name}-kb`       | Shared knowledge base      | `swarm-docs-kb`        |
| `task-{id}-spec`        | Task requirements          | `task-42-spec`         |
| `task-{id}-findings`    | Shared discoveries         | `task-42-findings`     |
| `{agent}-workspace`     | Private agent workspace    | `explorer-workspace`   |

### Basic Workflow

**1. Leader creates shared context:**

```python
load_context(content="Project: Analyze auth system", context_id="swarm-auth-kb")
```

**2. Spawn agents with Aleph access:**

```bash
# Each agent connects to the same Aleph MCP server
# They can all access "swarm-auth-kb"
```

**3. Agents write findings to shared context:**

```python
# Agent A finds something
exec_python(code="""
finding = "Auth uses JWT with RS256"
ctx_append(finding)
""", context_id="task-42-findings")
```

**4. Agents read each other's work:**

```python
search_context(pattern="JWT|token", context_id="task-42-findings")
```

**5. Diff and merge contexts:**

```python
diff_contexts(a="agent-a-workspace", b="agent-b-workspace")
```

### Self-Improvement Loop

Swarms can accumulate learnings across sessions:

```python
# After completing a task, log what worked
exec_python(code="""
learning = '''
## Pattern: Parallel Code Search
- Split codebase by directory
- Each agent searches one area
- Merge findings to shared context
- 3x faster than sequential
'''
ctx_append(learning)
""", context_id="swarm-kb")

# Save for next session
save_session(context_id="swarm-kb", path="swarm_learnings.json")
```

### Key Patterns

**Parallel exploration:**

```python
# Spawn multiple agents, each with a different context_id
# Agent 1: context_id="explore-frontend"
# Agent 2: context_id="explore-backend"
# All write findings to: context_id="task-findings"
```

**Consensus building:**

```python
# Each agent writes proposal to task-proposals
# Use diff_contexts to compare
# Synthesize with sub_aleph
```

**Knowledge propagation:**

```
Discovery -> Private Workspace -> Validate -> Shared Context -> Knowledge Base
```

### Environment Variables

| Variable                          | Description                                                        |
|-----------------------------------|--------------------------------------------------------------------|
| `ALEPH_SUB_QUERY_SHARE_SESSION`  | Set to `1` to let sub-agents access parent's MCP session           |
| `ALEPH_SUB_QUERY_BACKEND`        | Backend for `sub_query()`: `claude`, `codex`, `gemini`, or `api`   |

---

## AI Assistant Setup (Copy/Paste)

Paste this into any AI coding assistant to add Aleph (MCP server + `/aleph`
skill):

```
You are an AI coding assistant. Please set up Aleph (Model Context Protocol / MCP).

1) Add the Aleph MCP server config:
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise"]
    }
  }
}

2) Install the /aleph skill prompt:
- Claude Code: copy docs/prompts/aleph.md -> ~/.claude/commands/aleph.md
- Codex CLI:   copy docs/prompts/aleph.md -> ~/.codex/skills/aleph/SKILL.md
- Gemini CLI:  copy docs/prompts/aleph.md -> ~/.gemini/skills/aleph/SKILL.md
  Ensure ~/.gemini/settings.json has "experimental": { "skills": true } and restart.
If this client uses a different skill/command folder, ask me where to place it.

3) Verify: run get_status() or list_contexts().
If tools are namespaced, use mcp__aleph__get_status or mcp__aleph__list_contexts.

4) (Optional) Enable sub_query (recursive sub-agent):
- Quick: just say "use claude backend" -- the LLM will run set_backend("claude")
- Env var: set ALEPH_SUB_QUERY_BACKEND=claude|codex|gemini|api
- API backend: set ALEPH_SUB_QUERY_API_KEY + ALEPH_SUB_QUERY_MODEL
Runtime switching: the LLM can call set_backend() or configure() anytime -- no restart.

5) Use the skill: /aleph (Claude Code) or $aleph (Codex CLI).
Gemini CLI: /skills list (use /skills enable aleph if disabled).
```

---

## The `/aleph` Skill (`$aleph` in Codex CLI)

The skill prompt teaches your LLM how to use Aleph effectively. It provides
workflow patterns, tool guidance, and troubleshooting tips for both:

- `/aleph` in Claude Code
- `$aleph` in Codex CLI

Aleph works best when skill prompt + MCP server are both configured.

### What It Does

- Loads files into searchable in-memory contexts
- Tracks evidence with citations as you reason
- Supports semantic search and fast rg-based codebase search
- Enables recursive sub-queries for deep analysis
- Persists sessions for later resumption (memory packs)

### Simplest Use Case

Just point at a file in your prompt:

```bash
/aleph path/to/huge_log.txt
# or in Codex CLI:
$aleph path/to/huge_log.txt
```

Expected behavior: the model loads the file into Aleph memory and immediately
starts analysis (search/peek/exec) without manually copying file contents.

### Expected `$aleph` Behavior (Codex CLI)

When `$aleph` is working correctly, this should happen in one flow:

1. You run `$aleph <file-path>`.
2. The model calls `load_file(path=...)`.
3. It begins analysis with tools like `search_context`, `peek_context`, and
   `exec_python`.
4. It returns findings rather than asking you to paste the file manually.

Quick verification prompt:

```text
$aleph path/to/large_file.log
Then call list_contexts() and show the loaded context_id before analysis.
```

### How to Invoke

| Client      | Command   | Typical form                |
|-------------|-----------|-----------------------------|
| Claude Code | `/aleph`  | `/aleph path/to/file`       |
| Codex CLI   | `$aleph`  | `$aleph path/to/file`       |

For other clients, copy [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and
paste it at session start.

### Installing the Skill

**Option 1 -- Direct download** (simplest)

Download [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and save it to:

- **Claude Code:** `~/.claude/commands/aleph.md`
  (Windows: `%USERPROFILE%\.claude\commands\aleph.md`)
- **Codex CLI:** `~/.codex/skills/aleph/SKILL.md`
  (Windows: `%USERPROFILE%\.codex\skills\aleph\SKILL.md`)

**Option 2 -- From installed package**

<details>
<summary>macOS / Linux</summary>

```bash
# Claude Code
mkdir -p ~/.claude/commands
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" \
  ~/.claude/commands/aleph.md

# Codex CLI
mkdir -p ~/.codex/skills/aleph
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" \
  ~/.codex/skills/aleph/SKILL.md
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

---

## How It Works

```
+-----------------+    tool calls     +--------------------------+
|   LLM client    | ---------------> |  Aleph (Python, RAM)     |
|  (limited ctx)  | <--------------- |  search / peek / exec    |
+-----------------+   small results  +--------------------------+
```

1. **Load**    -- `load_context` (paste text) or `load_file` (from disk)
2. **Explore** -- `search_context`, `semantic_search`, `peek_context`
3. **Compute** -- `exec_python` with 100+ built-in helpers
4. **Reason**  -- `think`, `evaluate_progress`, `get_evidence`
5. **Persist** -- `save_session` to resume later

### Quick Example

```python
# Load log data
load_context(content=logs, context_id="logs")
# -> "Context loaded 'logs': 445 chars, 7 lines, ~111 tokens"

# Search for errors
search_context(pattern="ERROR", context_id="logs")
# -> Found 2 match(es):
#    Line 1: 2026-01-15 10:23:45 ERROR [auth] Failed login...
#    Line 4: 2026-01-15 10:24:15 ERROR [db] Connection timeout...

# Extract structured data
exec_python(code="emails = extract_emails(); print(emails)", context_id="logs")
# -> [{'value': 'user@example.com', 'line_num': 0, 'start': 50, 'end': 66}, ...]
```

### Advanced Workflows

**Multi-context workflow (code + docs + diffs):**

```python
# Load a design doc and a repo snapshot
load_context(content=design_doc_text, context_id="spec")
rg_search(pattern="AuthService|JWT|token", paths=["."],
          load_context_id="repo_hits", confirm=True)

# Compare or reconcile
diff_contexts(a="spec", b="repo_hits")
search_context(pattern="missing|TODO|mismatch", context_id="repo_hits")
```

**Advanced querying with `exec_python`:**

```python
# Treat exec_python as a reasoning tool, not just code execution
exec_python(code="print(extract_classes())", context_id="repo_hits")
```

## Recipe Pipelines

Recipes are declarative, multi-step pipelines that chain search, filter, sub-query, and aggregation operations. They can be defined as JSON payloads or built with a fluent Python DSL.

### Architecture

```
validate_recipe ──► estimate_recipe ──► run_recipe
       │                   │                 │
  normalize &         projected           execute
  check schema      cost/shape           pipeline
```

Recommended flow: **validate** (catch errors early) → **estimate** (preview cost) → **run** (execute).

### MCP Tools

| Tool | Purpose |
|------|---------|
| `validate_recipe` | Validate and normalize a recipe payload |
| `estimate_recipe` | Static cost/shape estimate (sub-query count, search hits) |
| `run_recipe` | Execute a JSON recipe pipeline |
| `compile_recipe` | Compile Recipe DSL code into a JSON recipe |
| `run_recipe_code` | Compile and execute DSL code in one call |

### JSON Recipe Example

```python
run_recipe(recipe={
    "version": "aleph.recipe.v1",
    "context_id": "logs",
    "budget": {"max_steps": 4, "max_sub_queries": 5},
    "steps": [
        {"op": "search", "pattern": "ERROR|WARN", "max_results": 10},
        {"op": "filter", "field": "match", "contains": "ERROR"},
        {"op": "take", "count": 1},
        {"op": "finalize"}
    ]
})
```

### Recipe DSL Example

The DSL provides a fluent builder that compiles to the same JSON format:

```python
run_recipe_code(
    context_id="logs",
    code="""
recipe = (
    Recipe(context_id='logs', max_sub_queries=5)
    .search('ERROR|WARN', max_results=10)
    .filter(field='match', contains='ERROR')
    .take(1)
    .finalize()
)
"""
)
```

DSL helpers available in `exec_python`: `Recipe`, `Search`, `Filter`, `MapSubQuery`, `Aggregate`, `Finalize`, `as_recipe`. Pipe syntax is also supported: `Recipe() | Search("ERROR") | Take(5) | Finalize()`.

### Supported Operations

| Op | Description |
|----|-------------|
| `search` | Regex search over context |
| `peek` / `lines` | Slice by char/line range |
| `take` | Limit result count |
| `chunk` | Split text into sized chunks (with optional `overlap`) |
| `filter` | Filter by regex `pattern` or `contains` on a `field` |
| `map_sub_query` | Fan-out: run a sub-query per result item |
| `sub_query` | Single sub-query on accumulated results |
| `aggregate` | Synthesize results via sub-query |
| `assign` / `load` | Store/retrieve named intermediate values |
| `finalize` | Mark pipeline complete |

### Recipe Cookbook

**Log Triage** — find errors, classify root causes:
```python
Recipe(context_id='logs', max_sub_queries=10)
    .search('ERROR|FATAL', max_results=10)
    .take(5)
    .map_sub_query('What is the root cause?', context_field='context')
    .aggregate('Prioritize these root causes')
    .finalize()
```

**Chunk & Summarize** — process large documents in pieces:
```python
Recipe(context_id='doc', max_sub_queries=5)
    .chunk(100000)
    .map_sub_query('Summarize this section')
    .aggregate('Combine into a unified summary')
    .finalize()
```

**Needle-in-Haystack** — search, narrow, extract (no sub-queries):
```python
Recipe(context_id='codebase')
    .search('TODO|FIXME|HACK|XXX', max_results=50)
    .filter(field='match', contains='HACK')
    .take(5)
    .finalize()
```

**Search & Summarize** — find all mentions, synthesize:
```python
Recipe(context_id='doc', max_sub_queries=1)
    .search('authentication|auth|login|JWT', max_results=15)
    .aggregate('How does authentication work?')
    .finalize()
```

**Multi-Perspective** — branch analysis with assign/load:
```python
Recipe(context_id='logs', max_sub_queries=3)
    .search('ERROR|WARN', max_results=20)
    .assign('all_issues')
    .filter(field='match', contains='ERROR')
    .sub_query('What patterns in these errors?')
    .assign('error_analysis')
    .load('all_issues')
    .filter(field='match', contains='WARN')
    .sub_query('What patterns in these warnings?')
    .aggregate('Compare error vs warning patterns')
    .finalize()
```

---

## Tools

**Core** (always available):
- `load_context`, `list_contexts`, `diff_contexts` — manage in-memory data
- `search_context`, `semantic_search`, `peek_context`, `chunk_context` — explore data; use `semantic_search` for concepts/fuzzy queries, `search_context` for precise regex
- `exec_python`, `get_variable` — compute in sandbox (100+ built-in helpers)
- `think`, `evaluate_progress`, `summarize_so_far`, `get_evidence`, `finalize` — structured reasoning
- `tasks` — lightweight task tracking per context
- `get_status` — session state
- `sub_query` — spawn recursive sub-agents (CLI or API backend)
- `sub_aleph` — nested Aleph recursion (RLM -> RLM)
- `validate_recipe`, `estimate_recipe`, `run_recipe`, `compile_recipe`, `run_recipe_code` — declarative recipe pipelines

**Action Tools** (requires `--enable-actions`):
- `load_file`, `read_file`, `write_file` — file I/O (PDFs, Word, HTML, .gz supported)
- `run_command`, `run_tests`, `rg_search` — shell tools
- `save_session`, `load_session` — persist state (memory packs)
- `add_remote_server`, `list_remote_tools`, `call_remote_tool` — MCP orchestration

<details>
<summary><strong>exec_python helpers (100+)</strong></summary>

The sandbox includes 100+ helpers that operate on the loaded context:

| Category | Examples |
|----------|----------|
| **Extractors** (25) | `extract_emails()`, `extract_urls()`, `extract_dates()`, `extract_ips()`, `extract_functions()` |
| **Statistics** (8) | `word_count()`, `line_count()`, `word_frequency()`, `ngrams()` |
| **Line operations** (12) | `head()`, `tail()`, `grep()`, `sort_lines()`, `columns()` |
| **Text manipulation** (15) | `replace_all()`, `between()`, `truncate()`, `slugify()` |
| **Validation** (7) | `is_email()`, `is_url()`, `is_json()`, `is_numeric()` |
| **Core** | `peek()`, `lines()`, `search()`, `chunk()`, `cite()`, `sub_query()`, `sub_aleph()`, `sub_query_map()`, `sub_query_batch()`, `sub_query_strict()`, `ctx_append()`, `ctx_set()` |
| **Recipe DSL** | `Recipe()`, `Search()`, `Chunk()`, `Filter()`, `MapSubQuery()`, `Aggregate()`, `Finalize()`, `as_recipe()` |

Extractors return `list[dict]` with keys: `value`, `line_num`, `start`, `end`.

</details>

---

## Configuration

### Workspace Controls

| Flag / Variable                  | Description                                                          |
|----------------------------------|----------------------------------------------------------------------|
| `--workspace-root <path>`        | Root for relative paths (default: git root from invocation cwd)      |
| `--workspace-mode <fixed\|git\|any>` | Path restrictions                                               |
| `--require-confirmation`         | Require `confirm=true` on action calls                               |
| `ALEPH_WORKSPACE_ROOT`           | Override workspace root via environment                              |

### Limits

| Flag                  | Default       | Description                   |
|-----------------------|---------------|-------------------------------|
| `--max-file-size`     | 1 GB          | Max file read                 |
| `--max-write-bytes`   | 100 MB        | Max file write                |
| `--timeout`           | 60 s          | Sandbox / command timeout     |
| `--max-output`        | 50,000 chars  | Max command output            |

### Recursion Budgets

| Variable                  | Default | Description                                    |
|---------------------------|---------|------------------------------------------------|
| `ALEPH_MAX_DEPTH`         | 2       | Max `sub_aleph` nesting depth                  |
| `ALEPH_MAX_ITERATIONS`    | 100     | Total RLM loop steps (root + recursion)        |
| `ALEPH_MAX_WALL_TIME`     | 300 s   | Wall-time cap per Aleph run                    |
| `ALEPH_MAX_SUB_QUERIES`   | 100     | Total `sub_query` calls allowed                |
| `ALEPH_MAX_TOKENS`        | unset   | Optional per-call output cap                   |

Override via environment variables or per-call args on `sub_aleph`. CLI backends
run `sub_aleph` as a single-shot call; use the API backend for full
multi-iteration recursion.

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all options.

---

## Documentation

| Document                                                 | Description                            |
|----------------------------------------------------------|----------------------------------------|
| [MCP_SETUP.md](MCP_SETUP.md)                            | Client configuration                   |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md)          | CLI flags and environment variables    |
| [docs/prompts/aleph.md](docs/prompts/aleph.md)          | Skill prompt and tool reference        |
| [CHANGELOG.md](CHANGELOG.md)                            | Release history                        |
| [DEVELOPMENT.md](DEVELOPMENT.md)                        | Contributing guide                     |

---

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e ".[dev,mcp]"
pytest
```

---

## References

> **Recursive Language Models**
> Zhang, A. L., Kraska, T., & Khattab, O. (2025)
> [arXiv:2512.24601](https://arxiv.org/abs/2512.24601)

## License

MIT

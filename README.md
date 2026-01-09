# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

Aleph is an MCP (Model Context Protocol) server that enables AI assistants to analyze documents too large for their context window. By implementing a Recursive Language Model (RLM) approach, it allows models to search, explore, and compute over massive datasets without exhausting their token limits.

## Key Capabilities

- **Unlimited Context**: Load files as large as your system RAM allows—gigabytes of data accessible via simple queries. The LLM never sees the raw file; it queries a Python process that holds the data in memory.
- **Navigation Tools**: High-performance regex search and line-based navigation.
- **Compute Sandbox**: Execute Python code over loaded content for parsing and analysis.
- **Evidence Tracking**: Automatic citation of source text for grounded answers.
- **Recursive Reasoning**: Spawn sub-agents to process document chunks in parallel.

### How "Unlimited Context" Works

Traditional LLMs are limited by their context window (~200K tokens). Aleph sidesteps this entirely:

```
┌─────────────────┐     queries      ┌─────────────────────────┐
│   LLM Context   │ ───────────────► │   Python Process (RAM)  │
│   (~200K tokens)│ ◄─────────────── │   (8GB, 32GB, 64GB...)  │
│                 │   small results  │   └── your_file.txt     │
└─────────────────┘                  └─────────────────────────┘
```

- **Python loads the entire file into RAM** as a string
- **The LLM queries it** via `search()`, `peek()`, `lines()`, etc.
- **Only query results** (kilobytes) enter the LLM's context—never the full file
- **Your RAM is the limit**, not the model's context window (with a default 1GB safety cap on action tools)

You can load **multiple files or entire repos** as separate contexts and query them independently.

A 50MB log file? The LLM sees ~1KB of search results. A 2GB database dump? Same—just the slices you ask for.

By default, Aleph sets a **1GB max file size** for action tools to avoid accidental overload, but you can raise it with `--max-file-size` based on your machine.
This cap applies to `load_file` / `read_file`; `load_context` still accepts any size you can supply in-memory.

## Installation

```bash
pip install "aleph-rlm[mcp]"
```

After installation, you can automatically configure popular MCP clients:

```bash
aleph-rlm install
```

## MCP Server

Run Aleph as an MCP server with:

```bash
aleph
```

Use `--enable-actions` to allow file and command tools.

## Integration

### Claude Desktop / Cursor / Windsurf

Add Aleph to your `mcpServers` configuration:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions"]
    }
  }
}
```

Install the `/aleph` skill for the RLM workflow prompt:
```bash
mkdir -p ~/.claude/commands
cp /path/to/aleph/docs/prompts/aleph.md ~/.claude/commands/aleph.md
```

Then use it like:
```
/aleph: Find the root cause of this test failure and propose a fix.
```

### Claude Code

To use Aleph with Claude Code, register the MCP server and install the workflow prompt:

```bash
# Register the MCP server
claude mcp add aleph aleph -- --enable-actions

# Add the workflow prompt
mkdir -p ~/.claude/commands
cp docs/prompts/aleph.md ~/.claude/commands/aleph.md
```

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.aleph]
command = "aleph"
args = ["--enable-actions"]
```

## How It Works

1. **Load**: Store a document in external memory via `load_context` or `load_file` (with `--enable-actions`).
2. **Explore**: Search for patterns using `search_context` or view slices with `peek_context`.
3. **Compute**: Run Python scripts over the content in a secure sandbox via `exec_python`.
4. **Finalize**: Generate an answer with linked evidence and citations using `finalize`.

## Recursion: Handling Very Large Inputs

When content is too large even for slice-based exploration, Aleph supports **recursive decomposition**:

1. **Chunk** the content into manageable pieces
2. **Spawn sub-agents** to analyze each chunk
3. **Synthesize** findings into a final answer

```python
# exec_python
chunks = chunk(100_000)  # split into ~100K char pieces
results = [sub_query("Extract key findings.", context_slice=c) for c in chunks]
final = sub_query("Synthesize into a summary:", context_slice="\n\n".join(results))
```

`sub_query` can use an API backend (OpenAI-compatible) or spawn a local CLI (Claude, Codex, Aider) - whichever is available.

### Sub-query backends

When `ALEPH_SUB_QUERY_BACKEND` is `auto` (default), Aleph chooses the first available backend:

1. **API** - if `MIMO_API_KEY` or `OPENAI_API_KEY` is available
2. **claude CLI** - if installed
3. **codex CLI** - if installed
4. **aider CLI** - if installed

Quick setup:

```bash
export ALEPH_SUB_QUERY_BACKEND=auto
export ALEPH_SUB_QUERY_MODEL=mimo-v2-flash
export MIMO_API_KEY=your_key

# Or use any OpenAI-compatible provider:
export OPENAI_API_KEY=your_key
export OPENAI_BASE_URL=https://api.xiaomimimo.com/v1
```

> **Note:** Some MCP clients don't reliably pass `env` vars from their config to the server process. If `sub_query` reports "API key not found" despite your client's MCP settings, add the exports to your shell profile (`~/.zshrc` or `~/.bashrc`) and restart your terminal/client.

For a full list of options, see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Available Tools

Aleph exposes the full toolset below.

### Core exploration
| Tool | Description |
|------|-------------|
| `load_context` | Store text or JSON in external memory. |
| `list_contexts` | List loaded contexts and metadata. |
| `peek_context` | View specific line or character ranges. |
| `search_context` | Perform regex searches with surrounding context. |
| `chunk_context` | Split content into navigable chunks. |
| `diff_contexts` | Diff two contexts (text or JSON). |
| `exec_python` | Run Python code over the loaded content. |
| `get_variable` | Retrieve a variable from the exec_python sandbox. |

### Reasoning workflow
| Tool | Description |
|------|-------------|
| `think` | Structure reasoning for complex problems. |
| `get_status` | Show current session state. |
| `get_evidence` | Retrieve collected citations. |
| `evaluate_progress` | Self-evaluate progress with convergence tracking. |
| `summarize_so_far` | Summarize progress on long tasks. |
| `finalize` | Complete with answer and evidence. |

### Recursion
| Tool | Description |
|------|-------------|
| `sub_query` | Spawn a sub-agent on a content slice. |

### Session management
| Tool | Description |
|------|-------------|
| `save_session` | Persist current session to file. |
| `load_session` | Load a saved session from file. |

### Recipes and reporting
| Tool | Description |
|------|-------------|
| `load_recipe` | Load an Alephfile recipe for execution. |
| `list_recipes` | List loaded recipes and status. |
| `finalize_recipe` | Finalize a recipe run and generate a result bundle. |
| `get_metrics` | Get token-efficiency metrics for a recipe/session. |
| `export_result` | Export a recipe result bundle to a file. |
| `sign_evidence` | Sign evidence bundles for verification. |

### Remote MCP orchestration
| Tool | Description |
|------|-------------|
| `add_remote_server` | Register a remote MCP server. |
| `list_remote_servers` | List registered remote MCP servers. |
| `list_remote_tools` | List tools available on a remote server. |
| `call_remote_tool` | Call a tool on a remote MCP server. |
| `close_remote_server` | Close a remote MCP server connection. |

### Action tools
*Enabled with the `--enable-actions` flag. Use `--workspace-root` and `--workspace-mode` (`fixed`, `git`, `any`) to control scope.*
| Tool | Description |
|------|-------------|
| `load_file` | Load a workspace file into a context. |
| `read_file` / `write_file` | File system access (workspace-scoped). |
| `run_command` | Shell execution. |
| `run_tests` | Execute test commands (supports optional `cwd`). |

## Configuration

For full configuration options (limits, budgets, and backend details), see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Changelog

### Unreleased

- **Unlimited context architecture**: Clarified that file size is limited by system RAM (with a default 1GB action-tool cap) rather than LLM context windows. Load gigabytes of data and query it with search/peek/lines.
- Added `--workspace-mode` for action tools (`fixed`, `git`, `any`) to support multi-repo workflows.
- Added optional `cwd` for `run_tests` to run tests outside the server’s default working directory.
- Updated MCP setup docs with multi-repo configuration examples.

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e ".[dev,mcp]"
pytest
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture details.

## License

MIT

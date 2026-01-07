# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

Aleph is an MCP (Model Context Protocol) server that enables AI assistants to analyze documents too large for their context window. By implementing a Recursive Language Model (RLM) approach, it allows models to search, explore, and compute over massive datasets without exhausting their token limits.

## Key Capabilities

- **External Memory**: Store massive documents outside model's context window.
- **Navigation Tools**: High-performance regex search and line-based navigation.
- **Compute Sandbox**: Execute Python code over loaded content for parsing and analysis.
- **Evidence Tracking**: Automatic citation of source text for grounded answers.
- **Recursive Reasoning**: Spawn sub-agents to process document chunks in parallel.

## Installation

```bash
pip install "aleph-rlm[mcp]"
```

After installation, you can automatically configure popular MCP clients:

```bash
aleph-rlm install
```

## MCP Server Options

Aleph provides two MCP servers with different capabilities:

| Feature | `aleph-mcp-local` (Recommended) | `aleph-mcp` (Advanced) |
|---------|-------------------------------|---------------------------|
| **Purpose** | Full-featured RLM server with action tools | Lightweight, stateless server |
| **Context Management** | In-memory session with evidence tracking | Stateless session dictionary |
| **Action Tools** | `load_file`, `write_file`, `run_command`, etc. (with `--enable-actions`) | Not available |
| **Sub-Query Backend** | Auto-detects CLI or API backends | Not available |
| **Evidence Tracking** | Full provenance with line ranges and citations | Basic tracking |
| **Best For** | Daily use, file operations, complex workflows | Custom integrations, minimal setup |

**Use `aleph-mcp-local` (recommended):**
- Most users and use cases
- Need file system access (`load_file`, `write_file`)
- Want automatic sub-query backend detection
- Need evidence tracking and session management

**Use `aleph-mcp` (advanced only):**
- Custom integration requiring stateless operation
- Lightweight deployment without dependencies
- Don't need action tools or sub-query

## Integration

### Claude Desktop / Cursor / Windsurf

Add Aleph to your `mcpServers` configuration:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph-mcp-local",
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
claude mcp add aleph aleph-mcp-local -- --enable-actions

# Add the workflow prompt
mkdir -p ~/.claude/commands
cp docs/prompts/aleph.md ~/.claude/commands/aleph.md
```

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.aleph]
command = "aleph-mcp-local"
args = ["--enable-actions"]
```

## How It Works

1. **Load**: Store a document in external memory via `load_context` or `load_file`.
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

`sub_query` can use an API backend (OpenAI-compatible) or spawn a local CLI (Claude, Codex, Aider)—whichever is available.

## Available Tools

### Core Exploration
|| Tool | Description |
||------|---------|
|| `load_context` | Store text or JSON in external memory. |
|| `load_file` | Load a workspace file into a context. |
|| `search_context` | Perform regex searches with surrounding context. |
|| `peek_context` | View specific line or character ranges. |
|| `exec_python` | Run Python code over the loaded content. |
|| `chunk_context` | Split content into navigable chunks. |

### Workflow Management
|| Tool | Description |
||------|---------|
|| `think` | Structure reasoning for complex problems. |
|| `get_status` | Show current session state. |
|| `get_evidence` | Retrieve collected citations. |
|| `evaluate_progress` | Self-evaluate progress with convergence tracking. |
|| `summarize_so_far` | Summarize progress on long tasks. |
|| `finalize` | Complete with answer and evidence. |

### Recursion
|| Tool | Description |
||------|---------|
|| `sub_query` | Spawn a sub-agent on a content slice. |

### Session Management
|| Tool | Description |
||------|---------|
|| `load_session` | Load a saved session from file. |
|| `save_session` | Persist current session to file. |

### Action Tools
*Enabled with the `--enable-actions` flag.*
|| Tool | Description |
||------|---------|
|| `read_file` / `write_file` | File system access (workspace-scoped). |
|| `run_command` | Shell execution. |
|| `run_tests` | Execute test commands. |
|| `add_remote_server` | Register remote MCP servers. |
|| `call_remote_tool` | Call tools on remote MCP servers. |
|| `close_remote_server` | Close remote MCP server connections. |

## Configuration

Sub-query backends and API keys can be configured via environment variables:

```bash
export ALEPH_SUB_QUERY_BACKEND=auto   # Options: auto, api, claude, codex, aider
export ALEPH_SUB_QUERY_MODEL=mimo-v2-flash  # Default: Mimo Flash V2 (free until Jan 20, 2026)
export MIMO_API_KEY=your_key          # API key for Mimo backend
export OPENAI_BASE_URL=https://api.xiaomimimo.com/v1  # Default endpoint
```

> **Note:** Some MCP clients don't reliably pass `env` vars from their config to the server process. If `sub_query` reports "API key not found" despite your client's MCP settings, add the exports to your shell profile (`~/.zshrc` or `~/.bashrc`) and restart your terminal/client.

For a full list of options, see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

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

# Aleph

Aleph is an MCP (Model Context Protocol) server that enables AI assistants to analyze documents too large for their context window. By implementing a Recursive Language Model (RLM) approach, it allows models to search, explore, and compute over massive datasets without exhausting their token limits.

## Key Capabilities

- **External Memory**: Store massive documents outside the model's context window.
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

## Available Tools

### Core Exploration
| Tool | Description |
| :--- | :--- |
| `load_context` | Store text or JSON in external memory. |
| `load_file` | Load a workspace file into a context. |
| `search_context` | Perform regex searches with surrounding context. |
| `peek_context` | View specific line or character ranges. |
| `exec_python` | Run Python code over the loaded content. |
| `chunk_context` | Split content into navigable chunks. |

### Workflow and Recursion
| Tool | Description |
| :--- | :--- |
| `think` | Document reasoning steps for complex tasks. |
| `sub_query` | Spawn a sub-agent to analyze a specific slice. |
| `get_evidence` | Retrieve all collected citations. |
| `finalize` | Complete the task with an answer and evidence. |

### Action Tools
*Enabled with the `--enable-actions` flag.*
| Tool | Description |
| :--- | :--- |
| `read_file` / `write_file` | Workspace-scoped file system access. |
| `run_command` | Execute shell commands. |
| `run_tests` | Run project test suites. |

## Configuration

Sub-query backends and API keys can be configured via environment variables:

```bash
export ALEPH_SUB_QUERY_BACKEND=auto   # Options: auto, api, claude, codex, aider
export OPENAI_API_KEY=your_key
export OPENAI_BASE_URL=https://api.openai.com/v1
export ALEPH_SUB_QUERY_MODEL=gpt-4o-mini
```

For a full list of options, see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e ".[dev,mcp]"
pytest
```

## License

MIT
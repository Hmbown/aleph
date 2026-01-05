# Aleph

> *"What my eyes beheld was simultaneous, but what I shall now write down will be successive, because language is successive."*
> — Jorge Luis Borges, ["The Aleph"](https://web.mit.edu/allanmc/www/borgesaleph.pdf) (1945)

**Aleph is an MCP server that lets AI assistants work with documents too large to fit in their context window.**

It implements the Recursive Language Model (RLM) paradigm from [arXiv:2512.24601](https://arxiv.org/abs/2512.24601).

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

---

## The problem

LLMs have a fundamental limitation: they can only "see" what fits in their context window. When you paste a large document into a prompt, models often miss important details buried in the middle—a phenomenon called "lost in the middle."

**The usual approach:**
1. Collect all relevant content
2. Paste it into the prompt
3. Hope the model attends to the right parts

**The RLM approach (what Aleph enables):**
1. Store content **outside** the model's context
2. Let the model **explore** it with tools (search, peek, compute)
3. Keep a trail of **evidence** linking outputs to source text
4. When needed, **recurse**: spawn sub-agents for chunks, then synthesize

Think of Borges' Aleph: a point containing all points. You don't hold it all in attention at once—you move through it, zooming and searching, returning with what matters.

---

## What Aleph provides

Aleph is an **[MCP](https://modelcontextprotocol.io/) server**—a standardized way for AI assistants to use external tools. It works with Claude Desktop, Cursor, Windsurf, VS Code, Claude Code, Codex CLI, and other MCP-compatible clients.

When you install Aleph, your AI assistant gains:

| Capability | What it means |
|------------|---------------|
| **External memory** | Store documents outside the context window as searchable state |
| **Navigation tools** | Search by regex, view specific line ranges, jump to matches |
| **Compute sandbox** | Run Python code over the loaded content (parsing, stats, transforms) |
| **Evidence tracking** | Automatically cite which parts of the source informed each answer |
| **Recursive agents** | Spawn sub-agents to process chunks in parallel, then aggregate |

The content you load can be anything representable as text or JSON: code repositories, build logs, incident reports, database exports, API responses, research papers, legal documents, etc.

---

## Quick start

```bash
pip install aleph-rlm[mcp]

# Auto-configure popular MCP clients
aleph-rlm install

# Verify installation
aleph-rlm doctor
```

<details>
<summary>Manual MCP configuration</summary>

Add to your MCP client config (Claude Desktop, Cursor, etc.):

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph-mcp-local"
    }
  }
}
```
</details>

<details>
<summary>Claude Code configuration</summary>

Claude Code auto-discovers MCP servers. Run `aleph-rlm install claude-code` or add to `~/.claude/settings.json`:

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
</details>

<details>
<summary>Codex CLI configuration</summary>

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.aleph]
command = "aleph-mcp-local"
args = []
```

Or run: `aleph-rlm install codex`

Install the `/aleph` skill for Codex:
```bash
mkdir -p ~/.codex/skills/aleph
cp /path/to/aleph/ALEPH.md ~/.codex/skills/aleph/SKILL.md
```
</details>

---

## How it works in practice

Once installed, you interact with Aleph through your AI assistant. Here's the typical flow:

### 1. Load your content

```
load_context(context="<your large document>", context_id="doc")
```

The assistant stores this externally—it doesn't consume context window tokens.

### 2. Explore with tools

```
search_context(pattern="error|exception|fail", context_id="doc")
peek_context(start=120, end=150, unit="lines", context_id="doc")
```

The assistant searches and views only the relevant slices.

### 3. Compute when needed

```python
# exec_python — runs in the sandbox with your content as `ctx`
matches = search(r"timeout.*\d+ seconds")
stats = {"total_matches": len(matches), "lines": [m["line_no"] for m in matches]}
```

### 4. Get cited answers

The assistant's final answer includes evidence trails back to specific source locations.

### Using the `/aleph` command

If you've installed the skill, just use:

```
/aleph: Find the root cause of this test failure and propose a fix.
```

For AI assistants using Aleph, see **[ALEPH.md](ALEPH.md)** for the detailed workflow.

---

## Recursion: handling very large inputs

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

---

## Available tools

**Core exploration:**
| Tool | Purpose |
|------|---------|
| `load_context` | Store text/JSON in external memory |
| `search_context` | Regex search with surrounding context |
| `peek_context` | View specific line or character ranges |
| `exec_python` | Run Python code over the content |
| `chunk_context` | Split content into navigable chunks |

**Workflow management:**
| Tool | Purpose |
|------|---------|
| `think` | Structure reasoning for complex problems |
| `get_evidence` | Retrieve collected citations |
| `summarize_so_far` | Summarize progress on long tasks |
| `finalize` | Complete with answer and evidence |

**Recursion:**
| Tool | Purpose |
|------|---------|
| `sub_query` | Spawn a sub-agent on a content slice |

**Optional actions** (disabled by default, enable with `--enable-actions`):
| Tool | Purpose |
|------|---------|
| `read_file`, `write_file` | File system access |
| `run_command`, `run_tests` | Shell execution |
| `save_session`, `load_session` | Persist/restore state |

---

## Configuration

Environment variables for `sub_query`:

```bash
# Backend selection (auto-detects by default)
export ALEPH_SUB_QUERY_BACKEND=auto   # or: api | claude | codex | aider

# API credentials (for API backend)
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
export ALEPH_SUB_QUERY_MODEL=gpt-4o-mini
```

> **Note:** Some MCP clients don't reliably pass `env` vars from their config to the server process. If `sub_query` reports "API key not found" despite your client's MCP settings, add the exports to your shell profile (`~/.zshrc` or `~/.bashrc`) and restart your terminal/client.

See **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** for all options.

---

## Security

- The Python sandbox is **best-effort, not hardened**—don't run untrusted code
- Action tools (file/command access) are **off by default** and workspace-scoped when enabled
- For untrusted inputs, run Aleph in a container with resource limits

---

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e '.[dev,mcp]'
pytest
```

See **[DEVELOPMENT.md](DEVELOPMENT.md)** for architecture details.

---

## License

MIT

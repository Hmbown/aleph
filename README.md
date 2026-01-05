# Aleph

> *"What my eyes beheld was simultaneous, but what I shall now write down will be successive, because language is successive."*
> — Jorge Luis Borges, ["The Aleph"](https://web.mit.edu/allanmc/www/borgesaleph.pdf) (1945)

**Aleph is an MCP server that implements the Recursive Language Model (RLM) paradigm** ([arXiv:2512.24601](https://arxiv.org/abs/2512.24601)).

The idea is simple to state and easy to miss in practice:

- Long-context failures are often *attention failures* (“lost in the middle”), not just window-size limits.
- Instead of packing more tokens into a prompt, treat the “context” as **external state**.
- Give the model tools to **search, compute, and recurse** over that state.

Aleph is that tool surface: it stores context in a sandboxed Python REPL as `ctx`, exposes navigation/search/compute tools over MCP, and (when needed) lets the model spawn sub-agents and aggregate their results.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

---

## The paradigm (RLM), not “bigger prompts”

Most LLM integrations still look like this:

1. Collect all the relevant stuff.
2. Paste it into the prompt.
3. Hope the model attends to the right parts.

RLM flips the default:

1. Store the relevant stuff **outside** the model (files, docs, logs, code, query results).
2. Let the model **explore** it programmatically (search/peek/compute).
3. Keep a trail of **evidence** so outputs can be traced back to source text.
4. When the problem is too large, **recurse**: spawn sub-agents for parts, then aggregate.

Borges’ Aleph is a useful mental model: a point that contains all points. You don’t *hold* the whole thing in attention at once; you move through it—zooming, searching, and returning with what matters.

---

## What Aleph is

Aleph is:

- **An MCP server** (works with Claude Desktop, Cursor, Windsurf, VS Code, Codex CLI, etc.).
- **An execution environment**: a sandboxed Python REPL where your context lives as `ctx`.
- **A navigation + compute surface** over that context (`search_context`, `peek_context`, `exec_python`, …).
- **A recursion primitive** (`sub_query`) for decomposing work and aggregating results.

Aleph is *not* tied to “document analysis” as a domain. The context you load can be anything you can represent as text/JSON:

- a repo snapshot (or selected files)
- build/test output
- incident logs
- database exports
- API responses
- evaluation datasets

---

## What this enables (practical outcomes)

### 1) Contexts far larger than the window
You can work against multi-megabyte inputs because the model is pulling *slices* on demand, not ingesting everything at once.

### 2) Grounded answers with citations
Aleph tracks evidence items (from search/peek/exec/sub-queries). You can require that every claim be supported by a source range.

### 3) Recursive decomposition (self-similar work)
When a task is too big to do in one pass, you chunk the context, run sub-agents on each chunk, and synthesize.

### 4) More than “read this PDF”
Because exploration is tool-driven, you can treat *any queryable surface* as context: code, logs, structured data, or the output of other tools.

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
<summary>Codex CLI configuration</summary>

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.aleph]
command = "aleph-mcp-local"
args = []
```

Or run: `aleph-rlm install codex`
</details>

---

## The basic loop (how you actually use it)

The habit Aleph is trying to enforce:

1. **Load** the context
2. **Search / peek** small slices
3. **Compute** where useful (regex, parsing, diffs, stats)
4. **Cite** what you rely on
5. **Finalize**

In an MCP client, that typically looks like:

### A sample user prompt (workflow-agnostic)

If your client supports it, you can frame requests like this to enforce the Aleph loop (load → search/peek → compute → cite → finalize), regardless of what the “context” is (docs, code, logs, datasets, API output):

```text
/aleph: Use Aleph to work over the attached context and produce a result that is grounded and auditable.

Requirements:
- Start by loading the context into Aleph (don’t paste it into the chat).
- Use search/peek to locate the relevant parts.
- Use exec_python for any parsing, counting, extraction, or cross-referencing.
- Cite every key claim to a line range / slice you inspected.
- Finalize with: (1) the result, (2) evidence/citations, (3) remaining unknowns + what you’d check next.
```

1) Load context

```text
load_context(context="<big text or JSON>", context_id="doc")
```

2) Search and inspect

```text
search_context(pattern="liabil|indemn|exclud", context_id="doc")
peek_context(start=120, end=170, unit="lines", context_id="doc")
```

3) Compute and cite (inside the sandbox)

```python
# exec_python
hits = search(r"indemnif|hold harmless")
for h in hits[:5]:
    cite(snippet=h["line"], line_range=(h["line_no"], h["line_no"]), note="candidate clause")
```

4) Finalize with an answer that can be audited

```text
finalize(answer="…", confidence="high", context_id="doc")
```

If you’re an AI assistant using Aleph, see **[ALEPH.md](ALEPH.md)** for the usage discipline.

---

## Recursion: when one pass isn’t enough

The “recursive” part of RLM is not a slogan; it’s an execution strategy.

When a problem is too large to handle as a single trajectory:

1. split the context into chunks
2. run `sub_query` on each chunk (sub-agents)
3. synthesize the results (often via another `sub_query`)

Sketch:

```python
# exec_python
chunks = chunk(100_000)  # chars
results = [sub_query("Extract obligations and exceptions.", context_slice=c) for c in chunks]
final = sub_query("Synthesize these findings into a single, cited answer:", context_slice="\n\n".join(results))
```

`sub_query` can use an API backend or spawn a local CLI (Claude/Codex/Aider), depending on what’s installed/configured.

---

## MCP tools (high-level)

Core exploration:

- `load_context` — store text/JSON as `ctx` in a sandboxed REPL
- `search_context` — regex search with surrounding context
- `peek_context` — view specific character/line ranges
- `exec_python` / `get_variable` — compute over the context
- `chunk_context` — produce navigable chunk boundaries + metadata

RLM control surface:

- `think`, `summarize_so_far`, `evaluate_progress` — manage long trajectories
- `get_evidence` — retrieve collected evidence
- `finalize` — complete with answer (plus evidence)

Recursion:

- `sub_query` — spawn a sub-agent on a slice of context

Optional “actions” (disabled by default; require starting with `--enable-actions`):

- `read_file`, `write_file`, `run_command`, `run_tests`, `save_session`, `load_session`

---

## Configuration

Common environment variables:

```bash
# Sub-query backend selection (auto picks based on what’s available)
export ALEPH_SUB_QUERY_BACKEND=auto   # or: api | claude | codex | aider

# API backend credentials (OpenAI-compatible)
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
export ALEPH_SUB_QUERY_MODEL=gpt-4o-mini
```

See **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** for the full list.

---

## Security notes

- The Python sandbox is **best-effort, not hardened**.
- “Action tools” (file/command access) are **off by default** and are workspace-scoped when enabled.
- For untrusted inputs, run Aleph in a container with resource limits.

---

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e '.[dev,mcp]'
pytest
```

Architecture notes: **[DEVELOPMENT.md](DEVELOPMENT.md)**

---

## License

MIT

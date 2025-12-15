# Aleph

> *"What my eyes beheld was simultaneous, but what I shall now write down will be successive, because language is successive."*
>
> — Jorge Luis Borges, ["The Aleph"](https://web.mit.edu/allanmc/www/borgesaleph.pdf) (1945)

Aleph is an MCP server for recursive LLM reasoning over documents. Instead of cramming context into a single prompt, the model iteratively explores it with search, code execution, and structured thinking tools—converging on answers with full provenance.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

## The Problem

Single-pass document analysis fails at scale. In a typical chat workflow, you paste/upload a document and the model reads it once into the context window. After that:

- **You can’t reliably “go back”**: It’s hard to re-check a claim against the source
- **Search is weak**: You can’t do targeted regex search across the full document
- **No real computation**: Numbers extracted from text often become “hand-wavy math”
- **No working memory**: There are no persistent variables/datasets across turns
- **No audit trail**: You can’t see exactly what text supported the conclusion
- **Context limits & attention dilution**: Long documents overflow windows and blur important details

## The Solution

Recursive exploration with provenance tracking:

```
CONTEXT (stored in REPL as `ctx`)
        │
        ▼
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│     LOAD      │────▶│    EXPLORE    │────▶│     CITE      │
│  Store once   │     │ search/peek/  │     │  Evidence     │
│  in sandbox   │     │ chunk/exec    │     │  accumulates  │
└───────────────┘     └───────────────┘     └───────┬───────┘
                              ▲                     │
                              │    ┌───────────┐    │
                              └────│ EVALUATE  │◀───┘
                                   │ progress  │
                                   └───────────┘
                                     │       │
                                   Low     High
                                     │       │
                                     ▼       ▼
                                 Continue  Finalize
                                           (with citations)
```

The model sees metadata about the context, not the full text. It writes Python code to explore what it needs, when it needs it. Each iteration lets the model refine its search based on what it learned, rather than betting everything on one attention pass. Evidence auto-accumulates. Final answers include citations.

## Quick Start

### MCP Setup (Claude Desktop, Cursor, Windsurf, VS Code, etc.)

```bash
pip install aleph-rlm[mcp]
aleph-rlm install
```

The installer auto-detects your MCP clients and configures them. Or install to a specific client:

```bash
aleph-rlm install claude-desktop
aleph-rlm install cursor
aleph-rlm install windsurf
aleph-rlm install claude-code
aleph-rlm doctor  # verify installation
```

<details>
<summary>Manual configuration (alternative)</summary>

Add to your MCP client config (example: Claude Desktop at `~/Library/Application Support/Claude/claude_desktop_config.json`):

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

Then use it:

```
You: Load this contract and find all liability exclusions

[AI calls load_context with document]
[AI calls search_context for "liab", "exclus", "indemnif"]
[AI calls cite() to tag key clauses]
[AI calls evaluate_progress → confidence 0.85]
[AI calls finalize with citations]

AI: Found 3 liability exclusions:
    1. Section 4.2: Consequential damages excluded (lines 142-158)
    2. Section 7.1: Force majeure carve-out (lines 289-301)
    3. Section 9.3: Cap at contract value (lines 445-452)

    Evidence: [4 citations with line ranges]
```

## Quick Example: Analyzing a 10-K Filing

### Without Aleph

You upload a filing and ask for a trend. The model tries to keep dozens of pages “in mind” at once. You often get:

- **Unverifiable claims** (no line-level citations)
- **Missed details** (attention dilution)
- **Approximate math** (no actual computation)

### With Aleph

You treat the filing like it’s “open in a notebook”:

```
You: Load this 10-K into Aleph

[AI calls load_context]

You: Find all R&D expense mentions

[AI calls search_context with pattern "R&D|research and development"]

You: Compute year-over-year growth from the extracted numbers

[AI calls exec_python to parse, store values, and compute]

You: Summarize the trend with citations

[AI calls finalize]
```

The key difference is that the document persists and the assistant can:

- **Verify** by peeking the exact lines it’s citing
- **Search** across the full text (regex)
- **Compute** in Python with persistent variables

## Common Workflows

### 1) Legal Document Review (find clauses, then cite)

```
You: Load this contract into Aleph (context_id="nda")
[AI calls load_context]

You: Find all liability / indemnification clauses
[AI calls search_context with pattern "liability|limitation of liability|indemnif|hold harmless"]

You: Pull the exact section and cite key language
[AI calls peek_context]
[AI calls exec_python to cite() specific snippets]
```

Inside `exec_python`, you can keep structured notes:

```python
findings = {
    "liability_cap": cite("cap", line_range=(1247, 1249)),
    "indemnity": cite("indemnification", line_range=(1302, 1315)),
}
```

### 2) Research Paper Analysis (locate methods/results, then compute)

```
You: Load this paper into Aleph (context_id="paper")
[AI calls load_context]

You: Find the methodology section
[AI calls search_context with pattern "method|approach|we propose"]

You: Verify the results table and compute the reported improvement
[AI calls peek_context]
[AI calls exec_python]
```

Example computation inside `exec_python`:

```python
baseline = 73.5
proposed = 89.2
improvement = ((proposed - baseline) / baseline) * 100
cite(f"{improvement:.1f}% improvement", note="computed from reported results")
```

### 3) Financial Analysis (extract figures, build a dataset, then calculate)

```
You: Load this 10-K into Aleph (context_id="aapl_10k")
[AI calls load_context]

You: Find revenue figures and the relevant discussion
[AI calls search_context with pattern r"\$[\d,]+\s*(million|billion)"]

You: Build a small table and compute YoY growth
[AI calls exec_python]

You: Provide a summary with citations
[AI calls finalize]
```

## MCP Tools

| Tool | Purpose |
|------|---------|
| `load_context` | Store document in sandboxed REPL as `ctx` |
| `peek_context` | View character or line ranges |
| `search_context` | Regex search with evidence logging |
| `exec_python` | Run code against context (includes `cite()` helper) |
| `chunk_context` | Split into navigable chunks with metadata |
| `think` | Structure reasoning sub-steps |
| `evaluate_progress` | Check confidence and convergence |
| `get_evidence` | Retrieve citation trail with filtering |
| `get_status` | Session state and metrics |
| `summarize_so_far` | Compress history to manage context |
| `finalize` | Complete with answer and citations |

## REPL Helpers

Available inside `exec_python`:

| Helper | Returns |
|--------|---------|
| `peek(start, end)` | Character slice as string |
| `lines(start, end)` | Line slice as string |
| `search(pattern, context_lines=2)` | `list[dict]` with `match`, `line_num`, `context` keys |
| `chunk(size, overlap=0)` | `list[str]` of text chunks |
| `cite(snippet, line_range, note)` | Citation dict (also logs to evidence) |

### Working with `search()` results

The `search()` helper returns structured data for programmatic use:

```python
# Find all mentions and iterate over them
results = search("liability|indemnif")
for r in results:
    print(f"Line {r['line_num']}: {r['match']}")

# Extract data and cite with provenance
for r in search(r"\$[\d,]+"):
    cite(r['match'], line_range=(r['line_num'], r['line_num']), note="dollar amount")

# Check type of any value
type(results)  # <class 'list'>
type(ctx)      # <class 'str'>
```

### Available builtins in `exec_python`

**Types:** `bool`, `int`, `float`, `str`, `dict`, `list`, `set`, `tuple`, `type`

**Functions:** `len`, `range`, `enumerate`, `zip`, `min`, `max`, `sum`, `sorted`, `reversed`, `any`, `all`, `abs`, `round`, `print`, `isinstance`

**Exceptions:** `Exception`, `ValueError`, `TypeError`, `RuntimeError`, `KeyError`, `IndexError`, `ZeroDivisionError`, `NameError`, `AttributeError`

**Allowed imports:** `re`, `json`, `csv`, `math`, `statistics`, `collections`, `itertools`, `functools`, `datetime`, `textwrap`, `difflib`

## Why It Works

| Problem | Single-Pass | Aleph |
|---------|-------------|-------|
| Large documents | Truncate or summarize | Load once, explore iteratively |
| Finding specifics | Scan everything | Targeted search |
| Verification | Trust the output | Evidence with line numbers |
| Context limits | Truncation required | Only fetch what's needed |
| Audit trail | None | Full citation history |

## When to Use Aleph

Use Aleph when:

- **The document is long** (e.g. >10 pages) or you have multiple documents
- **You need targeted search** for patterns, clauses, or repeated terms
- **You need computation** (tables, totals, growth rates, comparisons)
- **You want citations** with line ranges for verification/auditing
- **You’re doing iterative analysis** (build up understanding across turns)

## The Key Insight

Aleph gives your AI assistant working memory.

Without it, a model reads a long document like someone skimming on a phone: one pass, no notes, hoping it remembers.

With Aleph, the document stays open like a researcher’s workspace: it can search, verify, compute, and cite as it builds understanding.

## When NOT to Use Aleph

- **Short documents** that fit comfortably in context (~30k tokens or less)—single-pass is faster
- **Simple lookups** where you know exactly what you're searching for
- **Latency-critical applications** where iteration overhead matters

Aleph shines when documents exceed context limits, when you need auditable reasoning, or when the answer requires synthesizing information from multiple locations.

## Provenance Tracking

Every exploration action is logged:

- `search_context` matches record pattern and line ranges
- `cite()` lets you tag findings with notes
- `get_evidence` retrieves the full trail (filterable by source)
- `finalize` includes citations automatically

This makes Aleph suitable for auditable analysis: legal research, compliance review, technical due diligence.

## Installation

```bash
pip install aleph-rlm
```

Optional extras:

```bash
pip install aleph-rlm[mcp]     # MCP server support
pip install aleph-rlm[yaml]    # YAML config files
pip install aleph-rlm[rich]    # Better logging
```

For development:

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e '.[dev,mcp]'
pytest  # 190 tests
```

## Security

The sandbox is best-effort, not hardened.

**Blocked**: `open`, `os`, `subprocess`, `socket`, `eval`, `exec`, dunder access, imports outside allowlist

**Allowed imports**: `re`, `json`, `csv`, `math`, `statistics`, `collections`, `itertools`, `functools`, `datetime`, `textwrap`, `difflib`

**For production**: Run in a container with resource limits. Do not expose to untrusted users without additional isolation.

## Configuration

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `ALEPH_MAX_ITERATIONS` | Iteration limit |
| `ALEPH_MAX_COST` | Cost limit in USD |

> **Note:** Standalone Python API with direct Anthropic/OpenAI calls is coming soon. Currently Aleph works as an MCP server where the host AI provides reasoning.

### CLI Commands

```bash
aleph-rlm install              # Interactive installer
aleph-rlm install <client>     # Install to specific client
aleph-rlm install --all        # Install to all detected clients
aleph-rlm uninstall <client>   # Remove from client
aleph-rlm doctor               # Verify installation
```

Supported clients: `claude-desktop`, `cursor`, `windsurf`, `vscode`, `claude-code`

### MCP Server Options

```bash
aleph-mcp-local --timeout 30 --max-output 10000
```

## Recent Changes

### v0.1.3 (December 2025)

- Added `type` builtin to sandbox
- Added `NameError` and `AttributeError` exceptions to sandbox
- Improved README with workflow examples and documented available builtins

### v0.1.2 (December 2025)

- `aleph install` CLI for easy MCP client configuration
- Auto-detection of Claude Desktop, Cursor, Windsurf, VSCode, Claude Code
- `aleph doctor` command to verify installation

### v0.1.1 (December 2025)

- Initial public release
- 12 MCP tools for recursive reasoning
- Provenance tracking with `cite()` and `get_evidence`
- Convergence metrics in `evaluate_progress`
- Session compression with `summarize_so_far`
- 190 tests passing

## Research

Inspired by [Recursive Language Models](https://alexzhang13.github.io/blog/2025/rlm/) by Alex Zhang and Omar Khattab. The core insight: rather than solving context limits at the architecture level, let models partition context and make recursive calls to themselves, maintaining smaller individual context windows throughout.

## License

MIT

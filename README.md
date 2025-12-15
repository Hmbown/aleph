# Aleph

> *"What my eyes beheld was simultaneous, but what I shall now write down will be successive, because language is successive."*
>
> — Jorge Luis Borges, ["The Aleph"](https://web.mit.edu/allanmc/www/borgesaleph.pdf) (1945)

Aleph is an MCP server for recursive LLM reasoning over documents. Instead of cramming context into a single prompt, the model iteratively explores it with search, code execution, and structured thinking tools—converging on answers with full provenance.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

## The Problem

Single-pass document analysis fails at scale:

- **Context limits**: Large documents exceed context windows
- **Attention dilution**: Important details get lost in noise
- **No audit trail**: You can't see how the model reached its conclusion
- **Wasted tokens**: The entire document is processed even when only fragments matter

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

The model sees metadata about the context, not the full text. It writes Python code to explore what it needs, when it needs it. Evidence auto-accumulates. Final answers include citations.

## Quick Start

### MCP Setup (Claude Desktop, Cursor, etc.)

```bash
pip install aleph-rlm
```

Add to Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph-mcp-local"
    }
  }
}
```

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

### Python API

```python
from aleph import Aleph, Budget

aleph = Aleph(
    provider="anthropic",
    root_model="claude-sonnet-4-20250514",
    budget=Budget(max_cost_usd=1.0, max_iterations=20),
)

resp = await aleph.complete(
    query="What are the key risks?",
    context=large_document,
)

print(resp.answer)
print(f"Cost: ${resp.total_cost_usd:.4f}")
print(f"Iterations: {resp.total_iterations}")
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

| Helper | Usage |
|--------|-------|
| `peek(start, end)` | View character range |
| `lines(start, end)` | View line range |
| `search(pattern, context_lines=2)` | Regex search |
| `chunk(size, overlap=0)` | Split into chunks |
| `cite(snippet, line_range, note)` | Tag evidence with provenance |

## Why It Works

| Problem | Single-Pass | Aleph |
|---------|-------------|-------|
| Large documents | Truncate or summarize | Load once, explore iteratively |
| Finding specifics | Scan everything | Targeted search |
| Verification | Trust the output | Evidence with line numbers |
| Context limits | Hit the wall | Only fetch what's needed |
| Audit trail | None | Full citation history |

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
| `ANTHROPIC_API_KEY` | Anthropic API (for Python API mode) |
| `OPENAI_API_KEY` | OpenAI API (for Python API mode) |
| `ALEPH_MAX_ITERATIONS` | Iteration limit |
| `ALEPH_MAX_COST` | Cost limit in USD |

### MCP Server Options

```bash
aleph-mcp-local --timeout 30 --max-output 10000
```

## Recent Changes

### v0.1.1 (December 2025)

- Initial public release
- 12 MCP tools for recursive reasoning
- Provenance tracking with `cite()` and `get_evidence`
- Convergence metrics in `evaluate_progress`
- Session compression with `summarize_so_far`
- 190 tests passing

## Research

Inspired by work on Recursive Language Models by Alex Zhang and Omar Khattab at MIT.

## License

MIT

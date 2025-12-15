# Aleph

> *"What my eyes beheld was simultaneous, but what I shall now write down will be successive, because language is successive."*
> — Jorge Luis Borges, ["The Aleph"](https://web.mit.edu/allanmc/www/borgesaleph.pdf) (1945)

**MCP server for recursive LLM reasoning over documents.** Instead of cramming context into one prompt, the model iteratively explores with search, code execution, and structured thinking—converging on answers with citations.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

## Quick Start

```bash
pip install aleph-rlm[mcp]
aleph-rlm install        # auto-detects Claude Desktop, Cursor, Windsurf, VS Code
aleph-rlm doctor         # verify installation
```

<details>
<summary>Manual configuration</summary>

Add to your MCP client config:
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

## How It Works

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
                                        │
                              ┌─────────┴─────────┐
                              ▼                   ▼
                          Continue            Finalize
                                          (with citations)
```

The model sees metadata about context, not the full text. It writes Python to explore what it needs, refining searches based on what it learns. Evidence auto-accumulates.

## Example

```
You: Load this contract and find all liability exclusions

[AI calls load_context, search_context, cite(), evaluate_progress, finalize]

AI: Found 3 liability exclusions:
    1. Section 4.2: Consequential damages excluded (lines 142-158)
    2. Section 7.1: Force majeure carve-out (lines 289-301)
    3. Section 9.3: Cap at contract value (lines 445-452)

    Evidence: [4 citations with line ranges]
```

## When to Use

| Use Aleph | Skip Aleph |
|-----------|------------|
| Long documents (>10 pages) | Short docs (<30k tokens) |
| Need regex search | Simple lookups |
| Need computation on extracted data | Latency-critical apps |
| Want citations with line numbers | |
| Iterative analysis across turns | |

<details>
<summary><strong>MCP Tools Reference</strong></summary>

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

</details>

<details>
<summary><strong>REPL Helpers</strong> (available in exec_python)</summary>

| Helper | Returns |
|--------|---------|
| `peek(start, end)` | Character slice as string |
| `lines(start, end)` | Line slice as string |
| `search(pattern, context_lines=2)` | `list[dict]` with `match`, `line_num`, `context` |
| `chunk(size, overlap=0)` | `list[str]` of text chunks |
| `cite(snippet, line_range, note)` | Citation dict (also logs to evidence) |

```python
# Example: iterate over search results
for r in search("liability|indemnif"):
    print(f"Line {r['line_num']}: {r['match']}")
    cite(r['match'], line_range=(r['line_num'], r['line_num']))
```

</details>

<details>
<summary><strong>Sandbox Builtins</strong></summary>

**Types:** `bool`, `int`, `float`, `str`, `dict`, `list`, `set`, `tuple`, `type`

**Functions:** `len`, `range`, `enumerate`, `zip`, `min`, `max`, `sum`, `sorted`, `reversed`, `any`, `all`, `abs`, `round`, `print`, `isinstance`

**Exceptions:** `Exception`, `ValueError`, `TypeError`, `RuntimeError`, `KeyError`, `IndexError`, `ZeroDivisionError`, `NameError`, `AttributeError`

**Imports:** `re`, `json`, `csv`, `math`, `statistics`, `collections`, `itertools`, `functools`, `datetime`, `textwrap`, `difflib`

</details>

<details>
<summary><strong>Configuration</strong></summary>

**Environment Variables:**
| Variable | Purpose |
|----------|---------|
| `ALEPH_MAX_ITERATIONS` | Iteration limit |
| `ALEPH_MAX_COST` | Cost limit in USD |

**CLI Commands:**
```bash
aleph-rlm install              # Interactive installer
aleph-rlm install <client>     # Install to specific client
aleph-rlm uninstall <client>   # Remove from client
aleph-rlm doctor               # Verify installation
```

Supported clients: `claude-desktop`, `cursor`, `windsurf`, `vscode`, `claude-code`

</details>

<details>
<summary><strong>Security</strong></summary>

The sandbox is best-effort, not hardened.

**Blocked:** `open`, `os`, `subprocess`, `socket`, `eval`, `exec`, dunder access, imports outside allowlist

**For production:** Run in a container with resource limits. Do not expose to untrusted users without additional isolation.

</details>

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e '.[dev,mcp]'
pytest  # 190 tests
```

## Research

Inspired by [Recursive Language Models](https://alexzhang13.github.io/blog/2025/rlm/) by Alex Zhang and Omar Khattab.

## License

MIT

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
<summary><strong>REPL Helpers</strong> (80+ functions available in exec_python)</summary>

**Core:**
`peek`, `lines`, `search`, `chunk`, `cite`

**Extraction (auto-detect from context):**
`extract_numbers`, `extract_money`, `extract_percentages`, `extract_dates`, `extract_times`, `extract_timestamps`, `extract_emails`, `extract_urls`, `extract_ips`, `extract_phones`, `extract_paths`, `extract_env_vars`, `extract_versions`, `extract_uuids`, `extract_hashes`, `extract_hex`

**Code analysis:**
`extract_functions`, `extract_classes`, `extract_imports`, `extract_comments`, `extract_strings`, `extract_todos`

**Log analysis:**
`extract_log_levels`, `extract_exceptions`, `extract_json_objects`

**Statistics:**
`word_count`, `char_count`, `line_count`, `sentence_count`, `paragraph_count`, `unique_words`, `word_frequency`, `ngrams`

**Line operations (grep-like):**
`head`, `tail`, `grep`, `grep_v`, `grep_c`, `uniq`, `sort_lines`, `number_lines`, `strip_lines`, `blank_lines`, `non_blank_lines`, `columns`

**Text manipulation:**
`replace_all`, `split_by`, `between`, `before`, `after`, `truncate`, `wrap_text`, `indent_text`, `dedent_text`, `normalize_whitespace`, `remove_punctuation`, `to_lower`, `to_upper`, `to_title`

**Pattern matching:**
`contains`, `contains_any`, `contains_all`, `count_matches`, `find_all`, `first_match`

**Comparison:**
`diff`, `similarity`, `common_lines`, `diff_lines`

**Collections:**
`dedupe`, `flatten`, `first`, `last`, `take`, `drop`, `partition`, `group_by`, `frequency`, `sample_items`, `shuffle_items`

**Validation:**
`is_numeric`, `is_email`, `is_url`, `is_ip`, `is_uuid`, `is_json`, `is_blank`

**Conversion:**
`to_json`, `from_json`, `to_csv_row`, `from_csv_row`, `to_int`, `to_float`, `to_snake_case`, `to_camel_case`, `to_pascal_case`, `to_kebab_case`, `slugify`

```python
# Examples
emails = extract_emails()  # Auto-extracts from ctx
money = extract_money()    # Finds $1,234.56 patterns
errors = grep("ERROR")     # Filter lines
word_frequency(top_n=10)   # Most common words
```

</details>

<details>
<summary><strong>Sandbox Builtins</strong></summary>

**Types:** `bool`, `int`, `float`, `str`, `dict`, `list`, `set`, `tuple`, `type`, `frozenset`, `bytes`, `bytearray`, `complex`, `slice`, `object`

**Functions:** `len`, `range`, `enumerate`, `zip`, `map`, `filter`, `iter`, `next`, `callable`, `min`, `max`, `sum`, `sorted`, `reversed`, `any`, `all`, `abs`, `round`, `pow`, `divmod`, `repr`, `ascii`, `chr`, `ord`, `format`, `hex`, `oct`, `bin`, `print`, `isinstance`, `issubclass`, `hash`, `id`

**Exceptions:** `Exception`, `ValueError`, `TypeError`, `RuntimeError`, `KeyError`, `IndexError`, `ZeroDivisionError`, `NameError`, `AttributeError`, `StopIteration`, `AssertionError`, `LookupError`, `ArithmeticError`, `UnicodeError`

**Imports:** `re`, `json`, `csv`, `math`, `statistics`, `collections`, `itertools`, `functools`, `datetime`, `textwrap`, `difflib`, `random`, `string`, `hashlib`, `base64`, `urllib.parse`, `html`

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

## Recent Changes

### v0.2.0 (December 2025)

**80+ new REPL helpers** for document analysis:
- 16 extraction functions (emails, IPs, money, dates, phones, URLs, paths, versions, UUIDs, functions, classes, TODOs, log levels)
- 8 statistics (word/line/char count, word frequency, n-grams)
- 12 grep-like line operations (head, tail, grep, sort, uniq, columns)
- 15 text manipulation (replace, split, before/after, truncate, normalize)
- 6 pattern matching (contains, count_matches, find_all)
- 4 comparison (diff, similarity)
- 11 collection utilities (dedupe, flatten, group_by, frequency)
- 7 validators (is_email, is_url, is_ip, is_uuid, is_json)
- 11 converters (to_json, to_snake_case, slugify)

**30+ new builtins:** `map`, `filter`, `iter`, `next`, `repr`, `chr`, `ord`, `pow`, `divmod`, `hash`, `id`, `callable`, `frozenset`, `bytes`, `slice`...

**6 new allowed imports:** `random`, `string`, `hashlib`, `base64`, `urllib.parse`, `html`

### v0.1.3 (December 2025)

- Added `type` builtin to sandbox
- Added `NameError` and `AttributeError` exceptions

## Research

Inspired by [Recursive Language Models](https://alexzhang13.github.io/blog/2025/rlm/) by Alex Zhang and Omar Khattab.

## License

MIT

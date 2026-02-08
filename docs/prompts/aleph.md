---
name: aleph
description: /aleph - External memory workflow for large local data.
---

# /aleph - External Memory Workflow

TL;DR: Load large data into external memory, search it, reason in loops, and persist across sessions.

## Quick Start

```
# Test if Aleph is available
list_contexts()
```

If that works, the MCP server is running.

Instant pattern:

```
load_context(content="<paste huge content here>", context_id="doc")
search_context(pattern="keyword", context_id="doc")
finalize(answer="Found X at line Y", context_id="doc")
```

Note: tool names may appear as `mcp__aleph__load_context` in your MCP client.

## Instant RLM (Load File → Work)

The most common pattern: point at a file, let Aleph load it, immediately apply RLM reasoning.

```
load_file(path="path/to/large_file.md", context_id="doc")
search_context(pattern="relevant", context_id="doc")
exec_python(code="""
chunks = chunk(50000)
summaries = sub_query_batch("Summarize:", chunks)
print(summaries)
""", context_id="doc")
finalize(answer="...", context_id="doc")
```

When a user says `/aleph myfile.py` or `$aleph myfile.py`, load it and immediately begin this pattern.

### Depth Invocation

Users can request a specific recursion depth: `/aleph N file` where N controls strategy.

| Invocation | Depth | Strategy |
|-----------|-------|----------|
| `/aleph file.py` | 1 | Direct analysis — search/peek/exec_python, no sub-agents |
| `/aleph 2 file.py` | 2 | Parallel fan-out — `sub_query_batch`/`sub_query_map` over chunks |
| `/aleph 3 file.py` | 3 | Recursive (RLM^2) — sub-agents use exec_python+sub_query internally |
| `/aleph 4 file.py` | 4 | Deep recursion (RLM^3) — sub-agents spawning sub-agents |

For depth 3+, bump timeouts: `configure(sub_query_timeout=300, sandbox_timeout=300)`.

**Dynamic escalation:** Start at depth 1. If results are insufficient or the data is too large (>500K chars), escalate to depth 2. If sub-queries reveal more complexity, go to depth 3.

## Practical Defaults

- Use `output="json"` for structured results and `output="markdown"` for human-readable output.
- Action tools require starting the server with `--enable-actions` and may require `confirm=true`.
- For large docs, pair `chunk_context()` with `peek_context()` to navigate quickly.
- Use `rg_search()` for fast repo search and `semantic_search()` for meaning-based lookup.
- `load_file()` handles PDFs, Word docs, HTML, and compressed logs (.gz/.bz2/.xz).
- Save and resume long tasks with `save_session()` and `load_session()`.
- Memory packs auto-save to `.aleph/memory_pack.json` and auto-load on startup (actions enabled).
- To see full tool docstrings, start with `--tool-docs full` or set `ALEPH_TOOL_DOCS=full`.

## Core Patterns

### 1) Analyze Data
```
load_context(content=data_text, context_id="doc")
search_context(pattern="important|keyword|pattern", context_id="doc")
peek_context(start=100, end=150, unit="lines", context_id="doc")
finalize(answer="Analysis complete: ...", confidence="high", context_id="doc")
```

### 2) Compare Two Contexts
```
load_context(content=doc1, context_id="v1")
load_context(content=doc2, context_id="v2")
diff_contexts(a="v1", b="v2")
search_context(pattern="difference", context_id="v1")
search_context(pattern="difference", context_id="v2")
finalize(answer="Key differences: ...", context_id="v1")
```

### 3) Deep Reasoning Loop
```
load_context(content=problem, context_id="analysis")

think(question="What is the core issue?", context_id="analysis")
search_context(pattern="relevant", context_id="analysis")
evaluate_progress(
    current_understanding="I found X...",
    remaining_questions=["What about Y?"],
    confidence_score=0.7,
    context_id="analysis"
)

finalize(answer="Conclusion: ...", confidence="high", context_id="analysis")
```

### 4) Fast Repo Search (rg)
```
rg_search(pattern="TODO|FIXME", paths=["."], load_context_id="rg_hits", confirm=true)
search_context(pattern="TODO", context_id="rg_hits")
```

### 5) Semantic Search
```
semantic_search(query="login failure", context_id="doc", top_k=3)
peek_context(start=1200, end=1600, unit="chars", context_id="doc")
```

### 6) Recursion Strategies

Aleph supports recursive sub-agents at multiple depths. Choose based on data size and problem complexity.

**Depth 1: Direct Analysis** — No sub-agents. Use search/peek/exec_python directly.
Best for: < 200K chars, simple questions, targeted extraction.

```
load_file(path="file.py", context_id="doc")
search_context(pattern="def.*error", context_id="doc")
exec_python(code="print(extract_functions())", context_id="doc")
finalize(answer="Found 3 error handlers", context_id="doc")
```

**Depth 2: Parallel Fan-Out** — One level of sub-agents via `sub_query_batch`/`sub_query_map`.
Best for: 200K-2M chars, summarization, multi-section analysis.

```
exec_python(code="""
chunks = chunk(200000)  # ~200K per sub-agent
summaries = sub_query_batch("Summarize key findings:", chunks)
final = sub_query("Synthesize into 5 bullets:\\n" + "\\n".join(summaries))
print(final)
""", context_id="doc")
```

**Depth 3: Recursive (RLM^2)** — Sub-agents themselves use exec_python + sub_query.
Best for: 2M+ chars, complex multi-step reasoning, cross-referencing.

```
exec_python(code="""
result = sub_aleph(
    query="Find all security vulnerabilities and rank by severity",
    context_slice=ctx[:500000],
    max_depth=2
)
print(result)
""", context_id="doc")
```

**Depth 4: Deep Recursion (RLM^3)** — Sub-agents spawning their own sub-agents.
Best for: Massive datasets, hierarchical analysis, multi-pass verification.
Requires: `configure(sub_query_timeout=600, sandbox_timeout=600)`

```
exec_python(code="""
# Each sub_aleph agent can spawn its own sub-agents
sections = chunk(500000)
results = sub_query_map(
    [f"Deep-analyze section {i} for architectural patterns, "
     "using sub_query to verify each finding" for i in range(len(sections))],
    context_slices=sections,
    limit=5
)
final = sub_query("Synthesize all architectural findings:\\n" + "\\n".join(results))
print(final)
""", context_id="doc")
```

**Choosing a Strategy:**

| Data Size | Problem Type | Recommended Depth |
|-----------|-------------|-------------------|
| < 200K | Simple lookup, extraction | 1 (direct) |
| 200K-1M | Summarization, overview | 2 (parallel) |
| 1M-5M | Cross-referencing, analysis | 2-3 (parallel/recursive) |
| 5M+ | Deep investigation | 3-4 (recursive) |
| Any | Verification/debate | 3 (adversarial sub-agents) |

**Shared Session Context:** When sub-agents need access to the parent's loaded contexts:
```
configure(sub_query_share_session=true)
exec_python(code="""
# Sub-agents can now peek/search the parent's contexts
result = sub_query("Search for 'TODO' in the parent context and summarize")
print(result)
""", context_id="doc")
```
This is opt-in. Default is `false`. Only enable when sub-agents need parent state.

### 7) Recipe Pipelines (Declarative + DSL)
Use recipes when you want reusable, inspectable workflows.

**A) Run JSON recipe directly**
```
run_recipe(
  recipe={
    "version": "aleph.recipe.v1",
    "context_id": "doc",
    "budget": {"max_steps": 8, "max_sub_queries": 5},
    "steps": [
      {"op": "search", "pattern": "ERROR|WARN", "max_results": 10},
      {"op": "map_sub_query", "prompt": "Root cause?", "context_field": "context"},
      {"op": "aggregate", "prompt": "Synthesize causes"},
      {"op": "finalize"}
    ]
  }
)
```

**B) Compile + run DSL code**
```
run_recipe_code(
  context_id="doc",
  code="""
recipe = (
    Recipe(context_id='doc', max_sub_queries=5)
    .search('ERROR|WARN', max_results=10)
    .map_sub_query('Root cause?', context_field='context')
    .aggregate('Synthesize causes')
    .finalize()
)
"""
)
```

**C) Chunk & Summarize (large docs)**
```
run_recipe_code(
  context_id="doc",
  code="""
recipe = (
    Recipe(context_id='doc', max_sub_queries=5)
    .chunk(100000)
    .map_sub_query('Summarize this section')
    .aggregate('Combine into a unified summary')
    .finalize()
)
"""
)
```

**D) Needle-in-Haystack (no sub-queries)**
```
run_recipe_code(
  context_id="codebase",
  code="""
recipe = (
    Recipe(context_id='codebase')
    .search('TODO|FIXME|HACK|XXX', max_results=50)
    .filter(field='match', contains='HACK')
    .take(5)
    .finalize()
)
"""
)
```

Recommended flow: `validate_recipe` -> `estimate_recipe` -> `run_recipe`.

Ops: `search`, `peek`, `lines`, `take`, `chunk`, `filter`, `map_sub_query`, `sub_query`, `aggregate`, `assign`, `load`, `finalize`.

## Sub-Query Guidance (Single-Agent Patterns)

Use sub-queries inside `exec_python` so the recursion is driven by code (symbolic loops),
not by repeated tool calls. This follows the Recursive Language Model (RLM) paradigm.

### Batching Efficiency (CRITICAL)

Sub-LLMs can handle ~500K characters per call. **Batch aggressively to minimize API costs!**

| Context Size | Bad Approach | Good Approach |
|-------------|--------------|---------------|
| 1M chars | 1000 sub_query calls (1K each) | 5-10 calls (~100-200K each) |
| 100K chars | 100 sub_query calls (1K each) | 1-2 calls (~50-100K each) |

**Rule of thumb:** Aim for ~100-200K characters per sub_query call.

### Example 1: Basic Chunking

```
exec_python(code=\"\"\"
chunks = chunk(100000)  # 100K char chunks
summaries = sub_query_batch(\"Summarize this chunk:\", chunks)
final = sub_query_strict(
    f\"Combine summaries into 5 bullets: {summaries}\",
    validate_regex=r\"^- \",
    max_retries=2,
)
print(final)
\"\"\", context_id=\"doc\")
```

### Example 2: Iterative Document Analysis

For structured documents (books, papers, logs), iterate section-by-section:

```
exec_python(code=\"\"\"
query = "What caused the system failure?"
buffers = []

# Split by sections/headers
import re
sections = re.split(r'\\n## ', ctx)

for i, section in enumerate(sections):
    if len(section) < 500:  # skip tiny sections
        continue
    summary = sub_query(
        f"Extract info relevant to: {query}",
        context_slice=section
    )
    buffers.append(f"Section {i}: {summary}")
    print(f"Processed section {i}/{len(sections)}")

# Final aggregation
final = sub_query(
    f"Based on these findings, answer: {query}\\n\\n" + "\\n".join(buffers)
)
print(f"ANSWER: {final}")
\"\"\", context_id=\"doc\")
```

### Example 3: Regex-Targeted Sub-Queries

When you know what to look for, use regex to narrow down, then sub-query on hits:

```
exec_python(code=\"\"\"
# Find relevant sections first
hits = search(r"error|exception|failed", max_results=20)
answers = []

for hit in hits:
    # Get surrounding context (100 lines)
    start = max(0, hit['line_num'] - 50)
    end = hit['line_num'] + 50
    snippet = lines(start, end)

    answer = sub_query(
        f"What error occurred here and what's the root cause?",
        context_slice=snippet
    )
    answers.append(f"Line {hit['line_num']}: {answer}")

# Aggregate findings
print("\\n".join(answers))
\"\"\", context_id=\"doc\")
```

### Example 4: Answer Verification Pattern

Use sub-queries to verify answers and avoid context rot:

```
exec_python(code=\"\"\"
# First pass: extract candidate answer
candidate = sub_query("What is the magic number mentioned?", ctx[:200000])
print(f"Candidate: {candidate}")

# Verification pass: confirm with different context slice
verification = sub_query(
    f"Is '{candidate}' the correct magic number? Verify from this context.",
    ctx[200000:400000]
)
print(f"Verification: {verification}")
\"\"\", context_id=\"doc\")
```

If you need to parse results in `exec_python`, prefer line-based formats like `KEY: value`.

## Sub-Query Backend Configuration

Sub-queries require a backend. Configure once per session:

**Quick switch (REPL helper inside exec_python):**
```
exec_python(code="set_backend('kimi')")  # or 'claude', 'codex', 'gemini', 'api'
exec_python(code="print(get_config())")    # verify current settings
```

**MCP tool (direct call):**
```
configure(sub_query_backend="kimi")
configure(sub_query_share_session=false)
configure(sub_query_timeout=300)
configure(sandbox_timeout=300)  # increase exec_python timeout for sub-query loops
```

**Backend priority (auto mode):** codex → gemini → kimi → claude → api

**Per-call overrides:** `validate_regex` and `max_retries` in `sub_query_strict()` override env defaults.

When a user says "use claude backend" or "switch to gemini", call `set_backend()` or `configure()`.

## Tool Reference

### Core Tools (always available)

**Context Management:**
| Tool | Purpose |
|------|---------|
| `load_context` | Load text/data into external memory |
| `list_contexts` | See all loaded contexts |
| `diff_contexts` | Compare two contexts |

**Search & Navigation:**
| Tool | Purpose |
|------|---------|
| `search_context` | Regex search with surrounding context |
| `semantic_search` | Meaning-based search over the context |
| `peek_context` | View specific line/char ranges |
| `chunk_context` | Split into navigable chunks |

**Reasoning & Execution:**
| Tool | Purpose |
|------|---------|
| `think` | Structure a reasoning sub-step |
| `evaluate_progress` | Self-assess and decide whether to continue |
| `summarize_so_far` | Compress reasoning history |
| `exec_python` | Run code over content (100+ built-in helpers) |
| `get_variable` | Retrieve a variable from the sandbox |
| `get_status` | Session state |
| `get_evidence` | View citations |
| `finalize` | Complete with answer |
| `tasks` | Track tasks attached to a context |
| `sub_query` | Spawn a sub-agent for a chunk |
| `sub_aleph` | Run a nested Aleph call |
| `validate_recipe` | Validate recipe payload and normalize schema |
| `estimate_recipe` | Static estimate for recipe cost/shape |
| `run_recipe` | Execute a recipe payload |
| `compile_recipe` | Compile Recipe DSL code to JSON recipe |
| `run_recipe_code` | Compile and execute Recipe DSL code |
| `configure` | Adjust runtime settings (timeouts, backends, etc.) |

### Action Tools (requires `--enable-actions`)

**Filesystem:**
| Tool | Purpose |
|------|---------|
| `load_file` | Load file from disk (PDFs, Word, HTML, .gz, etc.) |
| `read_file` | Read file content (raw) |
| `write_file` | Write file content |

**Shell & Search:**
| Tool | Purpose |
|------|---------|
| `run_command` | Run a shell command |
| `run_tests` | Run test commands |
| `rg_search` | Fast repo-wide search (ripgrep) |

**Persistence:**
| Tool | Purpose |
|------|---------|
| `save_session` | Save state to file (memory packs) |
| `load_session` | Resume from file |

**Remote MCP Orchestration:**
| Tool | Purpose |
|------|---------|
| `add_remote_server` | Register MCP server |
| `list_remote_servers` | List registered servers |
| `list_remote_tools` | Discover tools |
| `call_remote_tool` | Execute remote tool |
| `close_remote_server` | Disconnect |

## exec_python Helpers

**Core:**
- `ctx`, `peek(start, end)`, `lines(start, end)`, `search(pattern)`, `chunk(size)`
- `ctx_append(text)` - append text to context (persists for subsequent operations)
- `ctx_set(text)` - replace entire context with new text
- `semantic_search(query, ...)` for meaning-based search
- `embed_text(text, dim)` for lightweight embeddings
- `extract_routes(lang="auto")` for route extraction
- `cite(snippet, line_range, note)` for evidence
- `sub_query(prompt, context_slice)` for recursion
- `sub_aleph(query, context=None)` for nested recursion
- `sub_query_map(prompts, context_slices=None, limit=None)` for batch sub-queries
- `sub_query_batch(prompt, context_slices, limit=None)` for one prompt over many slices
- `sub_query_strict(prompt, context_slice=None, validate_regex=None, max_retries=0)` for strict output validation
- Recipe DSL: `Recipe()`, `Search()`, `Chunk()`, `Filter()`, `MapSubQuery()`, `Aggregate()`, `Finalize()`, `as_recipe()`

**100+ built-in helpers** including:
- Extractors: `extract_emails()`, `extract_urls()`, `extract_dates()`, `extract_ips()`, `extract_functions()`
- Statistics: `word_count()`, `line_count()`, `word_frequency()`, `ngrams()`
- Line ops: `head()`, `tail()`, `grep()`, `sort_lines()`, `columns()`
- Validation: `is_email()`, `is_url()`, `is_json()`, `is_numeric()`

Extractors return `list[dict]` with keys: `value`, `line_num`, `start`, `end`.

## Configure Reference

The `configure()` tool adjusts runtime settings without restarting:

| Parameter | Type | Description |
|-----------|------|-------------|
| `sub_query_backend` | string | `kimi`, `claude`, `codex`, `gemini`, `api`, `auto` |
| `sub_query_share_session` | bool | Share live MCP context with sub-agents |
| `sub_query_timeout` | float | Timeout in seconds for sub-query calls |
| `sandbox_timeout` | float | Timeout in seconds for `exec_python` execution |
| `max_cmd_seconds` | float | Timeout for `run_command` shell commands |
| `tool_docs_mode` | string | `concise` or `full` tool documentation |

**Important:** Set `sub_query_share_session=false` unless you specifically need sub-agents to access parent Aleph contexts. Session sharing adds complexity and can cause issues with some backends.

## Troubleshooting

- "Tool not found": MCP server not running; check `aleph` command.
- "Context not found": verify `context_id` and use `list_contexts()`.
- Search returns nothing: broaden your regex pattern.
- `rg_search` slow: install ripgrep (`rg`) for best performance.
- Running out of context: use `summarize_so_far()` to compress.
- Session file not found: check the path relative to the server working dir.

---
name: aleph
description: /aleph - External memory workflow for large local data.
---

# /aleph - External Memory Workflow

TL;DR: Load large data into external memory, search it, reason in loops, and
persist across sessions.

This plugin bundles the Aleph MCP launcher and the Aleph skill. It assumes the
`aleph` executable is already installed and available on `PATH`.

## Quick Start

```text
# Test if Aleph is available
list_contexts()
```

If that works, the MCP server is running.

Instant pattern:

```text
load_context(content="<paste huge content here>", context_id="doc")
search_context(pattern="keyword", context_id="doc")
finalize(answer="Found X at line Y", context_id="doc")
```

Note: tool names may appear as `mcp__aleph__load_context` in your MCP client.

## Instant RLM (Load File -> Work)

The most common pattern: point at a file, let Aleph load it, and immediately
apply RLM reasoning.

```text
load_file(path="path/to/large_file.md", context_id="doc")
search_context(pattern="relevant", context_id="doc")
exec_python(code="""
chunks = chunk(50000)
summaries = sub_query_batch("Summarize:", chunks)
print(summaries)
""", context_id="doc")
finalize(answer="...", context_id="doc")
```

When a user says `/aleph myfile.py` or `$aleph myfile.py`, load it and
immediately begin this pattern.

### Depth Invocation

Users can request a specific recursion depth: `/aleph N file` where `N`
controls strategy.

| Invocation | Depth | Strategy |
|-----------|-------|----------|
| `/aleph file.py` | 1 | Direct analysis: search, peek, exec_python |
| `/aleph 2 file.py` | 2 | Parallel fan-out with `sub_query_batch` or `sub_query_map` |
| `/aleph 3 file.py` | 3 | Recursive `sub_aleph` usage |
| `/aleph 4 file.py` | 4 | Deep recursion with longer timeouts |

For depth 3+, bump timeouts:

```text
configure(sub_query_timeout=300, sandbox_timeout=300)
```

Dynamic escalation rule:

- Start at depth 1.
- Escalate to depth 2 when results are insufficient or the data is too large.
- Escalate to depth 3 when sub-queries expose additional structure that needs
  recursive reasoning.

## Practical Defaults

- Use `output="json"` for structured results and `output="markdown"` for
  human-readable output.
- For large docs, pair `chunk_context()` with `peek_context()` to navigate
  quickly.
- Use `rg_search()` for repo search and `semantic_search()` for meaning-based
  lookup.
- `load_file()` handles PDFs, Word docs, HTML, and compressed logs.
- Save and resume long tasks with `save_session()` and `load_session()`.
- Session paths should stay under Aleph's workspace root; `.aleph/` is a safe
  default.

## Core Patterns

### 1. Analyze Data

```text
load_context(content=data_text, context_id="doc")
search_context(pattern="important|keyword|pattern", context_id="doc")
peek_context(start=100, end=150, unit="lines", context_id="doc")
finalize(answer="Analysis complete: ...", confidence="high", context_id="doc")
```

### 2. Compare Contexts

```text
load_context(content=doc1, context_id="v1")
load_context(content=doc2, context_id="v2")
diff_contexts(a="v1", b="v2")
finalize(answer="Key differences: ...", context_id="v1")
```

### 3. Deep Reasoning Loop

```text
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

### 4. Fast Repo Search

```text
rg_search(pattern="TODO|FIXME", paths=["."], load_context_id="rg_hits", confirm=true)
search_context(pattern="TODO", context_id="rg_hits")
```

### 5. Semantic Search

```text
semantic_search(query="login failure", context_id="doc", top_k=3)
peek_context(start=1200, end=1600, unit="chars", context_id="doc")
```

### 6. Recursive Summarization

```text
exec_python(code="""
chunks = chunk(200000)
summaries = sub_query_batch("Summarize this section:", chunks)
final = sub_query("Synthesize into five bullets:\\n" + "\\n".join(summaries))
print(final)
""", context_id="doc")
```

### 7. Shared Session Context

When sub-agents need access to the parent's loaded contexts:

```text
configure(sub_query_share_session=true)
exec_python(code="""
result = sub_query("Search for TODOs in the parent context and summarize them")
print(result)
""", context_id="doc")
```

This is opt-in. Default is `false`.

## Recipe Pipelines

Use recipes when you want reusable, inspectable workflows.

### JSON Recipe

```text
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

### DSL Recipe

```text
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

## Safety Model

The safest default pattern is:

1. Load the large context into Aleph memory.
2. Search or compute inside Aleph.
3. Retrieve only the small result you need.

Prefer server-side computation over pulling raw context back through the prompt.
Do not treat `get_variable("ctx")` as the default path.

## References

- Repo: `https://github.com/Hmbown/aleph`
- Main docs: `README.md`, `MCP_SETUP.md`, `docs/CONFIGURATION.md`

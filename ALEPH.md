# Aleph Skill: RLM-Style Document Analysis

When analyzing large documents, use this recursive reasoning approach instead of trying to process everything at once.

## When to Use Aleph

- Document is too large to fit comfortably in context
- Need precise citations/evidence for claims
- Multi-step analysis requiring iteration
- Aggregating information across sections

## Core Pattern

```
1. Load context → 2. Chunk if needed → 3. Search/peek relevant parts → 4. sub_query for deep analysis → 5. Aggregate → 6. Finalize with citations
```

## Tools Available

| Tool | Use For |
|------|---------|
| `load_context` | Load the document into session |
| `peek_context` | View specific line/char ranges |
| `search_context` | Find patterns with regex |
| `exec_python` | Run code on context (80+ helpers available) |
| `sub_query` | Spawn sub-agent for chunk analysis (RLM) |
| `chunk_context` | Split into navigable chunks |
| `think` | Structure reasoning steps |
| `get_evidence` | Review collected citations |
| `finalize` | Complete with answer + evidence |

## RLM Pattern with sub_query

For documents too large to analyze at once:

```python
# In exec_python:
chunks = chunk(100000)  # 100k char chunks
summaries = []

for i, c in enumerate(chunks):
    result = sub_query(
        prompt=f"Analyze chunk {i+1}/{len(chunks)}: What are the key findings?",
        context_slice=c
    )
    summaries.append(result)

# Aggregate
final = sub_query(
    prompt="Synthesize these chunk summaries into a comprehensive answer:",
    context_slice="\n---\n".join(summaries)
)
print(final)
```

## Example Workflow

**User**: "Analyze this 500-page legal document for liability risks"

**AI Response**:

1. `load_context(document)` - Load the full document
2. `chunk_context(chunk_size=50000)` - Map into ~10 chunks
3. For each chunk, `sub_query("Identify liability risks in this section", context_slice=chunk)`
4. `exec_python` to aggregate findings
5. `search_context("indemnif|liabil|waiv")` - Find specific clauses
6. `finalize` with structured answer + evidence citations

## Key Principles

1. **Don't stuff context** - Use peek/search to access what you need
2. **Cite everything** - Use `cite()` helper to track evidence
3. **Decompose** - Break complex questions into sub-queries
4. **Iterate** - Use `evaluate_progress` to check if you have enough info
5. **Aggregate** - Combine chunk results into coherent answer

## sub_query Backends

The `sub_query` tool auto-detects the best backend:

1. **claude CLI** - Uses your Claude subscription (no extra API key)
2. **codex CLI** - Uses your OpenAI subscription
3. **aider CLI** - If installed
4. **Mimo API** - Free fallback (set `MIMO_API_KEY`)

In IDE environments (Windsurf, Cursor, Claude Code), CLI backends work automatically.
In Claude Desktop, you need the Mimo API key configured.

## Helpers in exec_python

The REPL has 80+ helpers. Key ones:

```python
# Text navigation
peek(0, 1000)           # First 1000 chars
lines(0, 50)            # First 50 lines
search(r"pattern")      # Regex search

# Extraction
extract_numbers(ctx)    # All numbers
extract_dates(ctx)      # All dates
extract_emails(ctx)     # All emails

# Analysis
word_frequency(ctx)     # Word counts
ngrams(ctx, 3)          # N-grams
diff(text1, text2)      # Compare texts

# Citation
cite("snippet", line_range=(10, 15), note="Key finding")
```

## Don't Do This

❌ Load entire document into a single prompt
❌ Guess at content without searching
❌ Skip citations
❌ Process sequentially when chunks are independent
❌ Ignore sub_query for complex analysis

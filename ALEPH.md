# /aleph — Document Analysis Skill

**Purpose:** Analyze documents without stuffing them into your context window.

## The Core Idea

When you receive a large document, don't paste it into your response context. Instead:

1. **Load** it into Aleph's external memory
2. **Search** for what you need
3. **Cite** what you find
4. **Finalize** with a grounded answer

You never see the full document. You explore it piece by piece, like a human would.

## When to Use This

| Use Aleph | Don't Use Aleph |
|-----------|-----------------|
| Document >30k tokens | Short text that fits in context |
| Need citations with line numbers | Quick answer without evidence |
| Must find specific information | General knowledge question |
| Analyzing sections across a long doc | Speed matters more than precision |

## The Process

```
load_context → search/peek → cite evidence → finalize
```

### 1. Load the Document

```
load_context(content="<document text>", context_id="doc1")
```

You receive metadata (size, preview) but NOT the full content. This protects your context window.
If the document lives in your workspace, you can also use:

```
load_file(path="docs/ARCHITECTURE.md", context_id="doc1")
```

### 2. Search for What You Need

```
search_context(pattern="liability|damages", context_id="doc1")
```

Returns matching lines with surrounding context (line numbers are 1-based by default). Use regex patterns.

### 3. View Specific Sections

```
peek_context(start=100, end=150, unit="lines", context_id="doc1")
```

Returns just lines 100-150 (end is inclusive). Only pull what you need.

### 4. Cite Evidence

Inside `exec_python`:

```python
cite(
    snippet="Contractor shall not be liable for consequential damages",
    line_range=(142, 145),
    note="Liability exclusion"
)
```

### 5. Finalize

```
finalize(
    answer="Found 3 liability exclusions...",
    confidence="high",
    context_id="doc1"
)
```

Returns your answer with all evidence attached.

## For Very Large Documents

If the document is huge (>100k chars), use `sub_query` to analyze chunks independently:

```python
# Inside exec_python
chunks = chunk(100000)  # Split into 100k char chunks

for i, c in enumerate(chunks):
    result = sub_query(
        prompt="What liability risks are in this section?",
        context_slice=c
    )
    print(f"Chunk {i+1}: {result}")
```

Each `sub_query` spawns an independent sub-agent. You aggregate the results.

## Tools Reference

| Tool | What It Does |
|------|--------------|
| `load_context` | Store document externally |
| `load_file` | Load a workspace file into a context (action tool) |
| `search_context` | Find patterns (regex) |
| `peek_context` | View specific lines/chars |
| `exec_python` | Run code on the document |
| `sub_query` | Analyze chunks independently |
| `chunk_context` | Get chunk boundaries |
| `get_evidence` | See all citations |
| `finalize` | Complete with answer + evidence |

## Helpers in exec_python

You have access to:

- `ctx` — the document
- `peek(start, end)` — view chars
- `lines(start, end)` — view lines
- `search(pattern)` — regex search
- `chunk(size)` — split into chunks
- `cite(snippet, line_range, note)` — track evidence
- `sub_query(prompt, context_slice)` — spawn sub-agent
- `allowed_imports()` — list allowed imports in the sandbox
- `is_import_allowed(name)` — check if an import is allowed
- `blocked_names()` — list forbidden builtin names

Plus 80+ extractors: `extract_emails()`, `extract_dates()`, `word_frequency()`, etc.

## Example

**User:** "Find the indemnification clauses in this contract"

**You do:**

1. `load_context(content=contract, context_id="contract")`
2. `search_context(pattern="indemnif|hold harmless", context_id="contract")`
3. `peek_context` on the matching line ranges
4. `exec_python` with `cite()` for each clause
5. `finalize` with list of clauses + line numbers

**You don't:**

- Paste the whole contract in your response
- Guess without searching
- Skip citations
- Try to hold it all in memory

## Configuration

`sub_query` backend priority (when `backend="auto"`):

1. **API** — if `MIMO_API_KEY` or `OPENAI_API_KEY` set
2. **claude CLI** — if installed
3. **codex CLI** — if installed
4. **aider CLI** — if installed

Override with environment variables:

```bash
export ALEPH_SUB_QUERY_BACKEND=api   # Force API
export MIMO_API_KEY=your_key         # API credentials
export ALEPH_SUB_QUERY_MODEL=gpt-4o  # Custom model
```

---
name: aleph
description: /aleph - External memory workflow for large local data.
---

# /aleph - External Memory Workflow

Core rule: keep whole contexts out of the prompt. Return only focused slices or
compact derived results.

This plugin bundles the Aleph MCP launcher, `/aleph` skill, `aleph-expert`
agent, and an install-check hook. It assumes the `aleph` executable is already
installed and available on `PATH`.

Note: tool names may appear as `mcp__aleph__load_workspace_manifest` in some
clients.

## The 5-Phase Loop

### 1. Load

Pick the correct front door:

- Large repo or codebase: `load_workspace_manifest(...)`
- Single large file: `load_file(...)`
- Inline or generated content: `load_context(...)`

Repo-scale default:

```text
load_workspace_manifest(paths=["src", "tests"], context_id="repo")
rg_search(
  pattern="FastAPI|APIRouter|router\\.",
  paths=["src", "tests"],
  load_context_id="routes"
)
load_file(path="pyproject.toml", context_id="pyproject")
```

Single-file default:

```text
load_file(path="/absolute/path/to/large.log", context_id="log")
search_context(pattern="ERROR|WARN", context_id="log")
peek_context(context_id="log", start=1, end=60, unit="lines")
```

Do not start repo work by reading files one by one when
`load_workspace_manifest(...)` is the right first move.

### 2. Orient

- Use `search_context(...)` to find relevant regions
- Use `peek_context(...)` to inspect small ranges
- Use `semantic_search(...)` for meaning-based lookup
- Use `chunk_context(...)` when navigability matters
- Use `rg_search(...)` to sweep repo trees quickly

Search before peeking. Pull only the slices you need.

### 3. Compute

- Use `exec_python(...)` for heavier analysis with `ctx` bound in the sandbox
- Aleph defaults to `output_feedback="full"`; `exec_python(...)` is not
  print-only
- In `full` mode Aleph can return stdout, stderr, error text, and a rendered
  return value
- If output volume becomes distracting, optionally use
  `configure(output_feedback="metadata")`
- Use built-in helpers such as `search`, `peek`, `lines`, `chunk`,
  `extract_*`, `semantic_search`, and `cite`
- Store compact derived values in variables like `summary`, `counts`,
  `matches`, or `result`
- Retrieve only those derived values with `get_variable(...)`
- Treat `get_variable("ctx")` as blocked for plugin workflows; use bounded
  slices or compact derived variables instead

Example:

```text
exec_python(code="""
matches = search(r"ERROR|WARN")
counts = {"matches": len(matches)}
summary = f"{counts['matches']} matching lines"
""", context_id="log")
get_variable(name="summary", context_id="log")
```

### 4. Recurse

Real recursion helper signatures:

```text
sub_query(prompt, context_slice=None)
sub_query_batch(prompt, context_slices, limit=None)
sub_query_map(prompts, context_slices=None, limit=None, parallel=True)
sub_aleph(query, context=None)
```

Runtime guidance:

- Use `sub_query_batch(...)` for one prompt over many slices
- Use `sub_query_map(...)` for distinct prompts, keeping `parallel=True` unless
  you need sequential execution
- Use `configure(sub_query_share_session=true)` when nested agents need access
  to parent contexts
- For depth 3+, use `configure(sub_query_timeout=300, sandbox_timeout=300)`

### 5. Converge

- Use `evaluate_progress(...)` when the answer is not yet stable
- Loop back through orient and compute when confidence is low
- Use `summarize_so_far(...)` if the trajectory is getting long
- Use `finalize(answer=..., confidence=..., context_id=...)` when done

## Depth Invocation

Users can request a specific recursion depth with `/aleph N target`.

| Invocation | Depth | Strategy |
|-----------|-------|----------|
| `/aleph file.py` | 1 | Direct file analysis with `load_file`, `search_context`, `peek_context`, `exec_python` |
| `/aleph repo/` | 1 | Repo analysis with `load_workspace_manifest`, `rg_search`, targeted `load_file`, `exec_python` |
| `/aleph 2 file.py` | 2 | Fan-out with `sub_query_batch` or `sub_query_map` |
| `/aleph 3 file.py` | 3 | Recursive `sub_aleph` with longer timeouts |
| `/aleph 4 file.py` | 4 | Deep recursion with explicit timeout tuning |

Escalation rule:

- Start at depth 1
- Move to depth 2 when the answer needs chunk-level fan-out
- Move to depth 3 or 4 only when nested structure or recursive synthesis is
  required

## Repo vs File Workflow

When the user points at a repo, codebase, or project tree:

- Start with `load_workspace_manifest(...)`
- Use `rg_search(...)` to locate candidate files
- Load only the files you actually need with `load_file(...)`
- Compute inside Aleph instead of pasting file contents into the prompt

When the user points at one large file:

- Start with `load_file(...)`
- Search, peek, and compute inside Aleph
- Pull back only the answer, a small slice, or a compact derived value

## Anti-Patterns

- Using `read_file(...)` as the default entry point for large files or repos
- Loading a whole repo file-by-file when `load_workspace_manifest(...)` is the
  better first step
- Treating `get_variable("ctx")` as a valid plugin workflow
- Pasting raw file or repo content into the prompt when Aleph can search or
  compute instead
- Claiming that `exec_python(...)` only returns `print()` output
- Pinning a nested API backend in the checked-in plugin wrapper

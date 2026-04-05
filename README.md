# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

Aleph is an [MCP server](https://modelcontextprotocol.io/) and skill for
**Recursive Language Models** (RLMs). It keeps working state — search indexes,
code execution, evidence, recursion — in a Python process outside the prompt
window, so the LLM reasons iteratively over large codebases, long-lived
projects, logs, documents, and data without burning context on raw content.

```text
+-----------------+    tool calls     +-----------------------------+
|   LLM client    | ---------------> |  Aleph (Python process)     |
| (context budget)| <--------------- |  search / peek / exec / sub |
+-----------------+   small results  +-----------------------------+
```

Why Aleph:

- **Load once, reason many times.** Data lives in Aleph memory, not the prompt.
- **Compute server-side.** `exec_python` runs code over the full context and
  returns only derived results. For JS/TS repos, `exec_javascript` and
  `exec_typescript` provide a persistent Node.js runtime over the same `ctx`.
- **Recurse.** Sub-queries and recipes split complex work across multiple
  reasoning passes.
- **Keep workspaces warm.** Bind contexts back to files or generated workspace
  manifests, refresh them, and resume long investigations later.

## Quick Start

```bash
pip install "aleph-rlm[mcp]"
aleph-rlm install --profile claude   # or: codex, portable, api
aleph-rlm doctor                     # verify everything is wired up
```

Then restart your MCP client and confirm Aleph is available:

```text
get_status()
list_contexts()
```

The optional `/aleph` (Claude Code) or `$aleph` (Codex) skill shortcut starts
a structured RLM workflow. Install
[`docs/prompts/aleph.md`](docs/prompts/aleph.md) into your client's
command/skill folder — see [MCP_SETUP.md](MCP_SETUP.md) for exact paths.

If you are using action tools on a real repo, the safest default is:

```bash
aleph --enable-actions --action-policy read-only
```

### Cursor

Use **global** MCP (`aleph-rlm install cursor`) for `--workspace-mode any`, or
**project** MCP (`aleph-rlm install cursor-project` from the repo) for
`${workspaceFolder}` + `--workspace-mode fixed`. Chat, Composer, and the Cursor
CLI share that MCP config; a Cursor extension is optional and not required for
Aleph — see [MCP_SETUP.md](MCP_SETUP.md#cursor).

## Entry Points

| Command | Module | What it does |
|---------|--------|--------------|
| `aleph` | `aleph.mcp.local_server:main` | **MCP server.** This is what MCP clients launch. Exposes 30+ tools for context management, search, code execution, reasoning, recursion, and action tools. |
| `aleph-rlm` | `aleph.cli:main` | **Installer and CLI.** `install`, `configure`, `doctor`, `uninstall` for setting up MCP clients. Also: `run` (single query), `shell` (interactive REPL), `serve` (start MCP server manually). |

## Install Profiles

`aleph-rlm install` asks which sub-query profile to use. Profiles configure
the nested backend that `sub_query` and `sub_query_batch` spawn for recursive
reasoning.

| Profile | What it pins |
|---------|-------------|
| `portable` | No nested backend — you choose later or rely on auto-detection |
| `claude` | Claude CLI: `--model opus`, `--effort low`, shared session enabled |
| `codex` | Codex MCP: `gpt-5.4`, low reasoning effort, shared session enabled |
| `api` | OpenAI-compatible API — set `ALEPH_SUB_QUERY_API_KEY` and `ALEPH_SUB_QUERY_MODEL` |

```bash
aleph-rlm install claude-code --profile claude
aleph-rlm configure --profile codex   # overwrite existing config
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all env vars, CLI
flags, and runtime `configure(...)` options.

## Large Codebase Workflow

If your main use case is a repo or multi-folder project, start by loading a
compact workspace manifest instead of throwing raw source files into the model
window. That gives the model a map of the project, lets it search aggressively,
and keeps the session refreshable as the repo changes.

```python
load_workspace_manifest(paths=["src", "tests"], context_id="repo")
rg_search(pattern="FastAPI|APIRouter|router\\.", paths=["src", "tests"], load_context_id="routes")
load_file(path="pyproject.toml", context_id="pyproject")
exec_python(code="""
files = [line for line in ctx.splitlines() if line.startswith("- ")]
summary = {
    "indexed_entries": len(files),
    "top_python_files": [line for line in files if "| python |" in line][:10],
}
""", context_id="repo")
get_variable(name="summary", context_id="repo")
refresh_context(context_id="repo")
```

Use `load_workspace_manifest` as the default front door for large codebases and
projects. Then pull in specific files with `load_file`, search the repo with
`rg_search`, and refresh the bound context when the workspace changes. Refreshes
preserve the session's reasoning state, evidence log, and tracked tasks.

### Single File Workflow

Aleph is also strong when you load one large file once, do the heavy work
inside Aleph, and only pull back compact answers.

```python
load_file(path="/absolute/path/to/large_file.log", context_id="doc")
search_context(pattern="ERROR|WARN", context_id="doc")
peek_context(start=1, end=60, unit="lines", context_id="doc")
exec_python(code="""
errors = [line for line in ctx.splitlines() if "error" in line.lower()]
result = {
    "error_count": len(errors),
    "first_error": errors[0] if errors else None,
}
""", context_id="doc")
get_variable(name="result", context_id="doc")
save_session(context_id="doc", path=".aleph/doc.json")
```

The important habit is to compute server-side. Do not treat `get_variable("ctx")`
as the default path. Search, filter, chunk, or summarize first, then retrieve a
small result.

If you want terminal-only mode instead of MCP, use:

```bash
aleph run "Summarize this log" --provider cli --model codex --context-file app.log
```

## Local Models (llama.cpp)

Aleph can use a local model instead of a cloud API. This runs the full RLM
loop — search, code execution, convergence — entirely on your machine with
zero API cost.

**Prerequisites:** [llama.cpp](https://github.com/ggml-org/llama.cpp) and a
GGUF model file.

```bash
# Install llama.cpp
brew install llama.cpp          # Mac
winget install ggml.LlamaCpp    # Windows

# Start the server with your model
llama-server -m /path/to/model.gguf -c 16384 -ngl 99 --port 8080
```

Point Aleph at the running server:

```bash
export ALEPH_PROVIDER=llamacpp
export ALEPH_LLAMACPP_URL=http://127.0.0.1:8080
export ALEPH_MODEL=local
aleph
```

Or let Aleph start the server automatically:

```bash
export ALEPH_PROVIDER=llamacpp
export ALEPH_LLAMACPP_MODEL=/path/to/model.gguf
export ALEPH_LLAMACPP_CTX=16384
export ALEPH_MODEL=local
aleph
```

Tested with Qwen 3.5 9B (Q8_0, ~9 GB). Any GGUF model works — larger models
give better results in the RLM loop. Models with reasoning/thinking support
(Qwen 3.5, QwQ, etc.) are handled automatically. See
[CONFIGURATION.md](docs/CONFIGURATION.md) for all `ALEPH_LLAMACPP_*`
variables.

## Common Workloads

| Scenario | What Aleph Is Good At |
|---|---|
| Large codebase / project analysis | Build a workspace map, search quickly, load only the files that matter, and keep the session refreshable |
| Large log analysis | Load big files, trace patterns, correlate events |
| Codebase navigation | Search symbols, inspect routes, trace behavior |
| Data exploration | Analyze JSON, CSV, and mixed text with Python helpers |
| Long document review | Load PDFs, Word docs, HTML, and compressed logs |
| Recursive investigations | Split work into sub-queries instead of one giant prompt |
| Long-running sessions | Save and resume memory packs across sessions |

## Core Tools

| Category | Primary tools | What they do |
|---|---|---|
| Load context | `load_context`, `load_file`, `load_workspace_manifest`, `refresh_context`, `list_contexts`, `diff_contexts` | Put data into Aleph memory, bind it back to workspace assets, and inspect what is loaded |
| Navigate | `search_context`, `semantic_search`, `peek_context`, `chunk_context`, `rg_search` | Find the relevant slice before asking for an answer |
| Compute | `exec_python`, `exec_javascript`, `exec_typescript`, `get_variable` | Run Python or JS/TS over the full context and retrieve only the derived result |
| Reason | `think`, `evaluate_progress`, `get_evidence`, `finalize` | Structure progress and close out with evidence |
| Orchestrate | `configure`, `validate_recipe`, `estimate_recipe`, `run_recipe`, `run_recipe_code` | Switch backends and automate repeated reasoning patterns |
| Persist | `save_session`, `load_session` | Keep long investigations outside the prompt window |

## Python vs JS/TS REPL

Aleph's **primary control layer is still Python**. `exec_python` remains the
default REPL for general-purpose analysis, recipes, and orchestration.

- Use `exec_python` when you need the full Aleph surface area: Python-first
  prompts, Python's numeric / symbolic stack (`cmath`, `mpmath`, `decimal`,
  `fractions`, `statistics`, `numpy`, `scipy`, `sympy`, `networkx`), or
  recipe execution via `run_recipe_code`.
- Use `exec_javascript` / `exec_typescript` when the target repo or analysis is
  naturally JS/TS-shaped and you want persistent Node state, JS-native array /
  object manipulation, or async recursion with `await`.

- `exec_python`
  Full Aleph helper surface, including recipe DSL helpers, synchronous
  `sub_query(...)` / `sub_aleph(...)`, and the widest compatibility with
  existing prompts and workflows.
- `exec_javascript` / `exec_typescript`
  Persistent Node.js runtime per context for JS/TS-heavy repos. Shares the same
  `ctx`, supports top-level `await`, and can recurse with async
  `await sub_query(...)`, `await sub_query_batch(...)`,
  `await sub_query_map(...)`, `await sub_query_strict(...)`, and
  `await sub_aleph(...)`. Also includes the recipe DSL (`Recipe`, `Search`,
  `Take`, etc.) for building recipe payloads in JS/TS.

The JS/TS runtime also ships with a broader local helper set than the first
handoff slice: search/peek/lines/chunk, extraction helpers (`extract_emails`,
`extract_todos`, `extract_routes`, etc.), text utilities (`number_lines`,
`grep_v`, `sort_lines`, `normalize_whitespace`, etc.), text comparison helpers
(`diff`, `similarity`, `common_lines`, `diff_lines`), collection helpers
(`flatten`, `group_by`, `frequency`, `sample_items`, `shuffle_items`, etc.),
validation helpers (`is_json`, `is_email`, `is_uuid`, etc.), CSV / JSON
converters, and `semantic_search`.

The JS/TS runtime now also includes the Recipe DSL: `RecipeStep`,
`RecipeBuilder`, and all step constructors (`Recipe`, `Search`, `Peek`,
`Lines`, `Take`, `Chunk`, `Filter`, `MapSubQuery`, `SubQuery`, `Aggregate`,
`Assign`, `Load`, `Finalize`, `as_recipe`). You can build recipes with
fluent chaining or pipe-style:

```javascript
// Fluent style
Recipe("doc").search("ERROR").take(5).finalize().compile()

// Pipe style
Recipe("doc").pipe(Search("ERROR")).pipe(Take(5)).pipe(Finalize()).compile()
```

The `compile_recipe` and `run_recipe_code` MCP tools accept a `language`
parameter (`"python"`, `"javascript"`, `"typescript"`) to compile recipe DSL
code in the corresponding runtime.

What still differs from Python:

- Python is still the default and best-supported Aleph REPL.
- JS/TS recursion helpers are async and require `await`.
- Recipe *execution* (`run_recipe`) always uses the Python runtime. The JS/TS
  path covers recipe *building and compilation* only.
- JS uses `RecipeBuilder.pipe()` / fluent methods instead of Python's `|`
  operator (JS `|` is bitwise OR, not overloadable for this purpose).
- Python's import ecosystem remains Python-only. The Node runtime is helper-led:
  no `require`, no `process`, no `module`, and no npm package loading inside
  the sandbox.
- `exec_typescript` strips type syntax for execution; it is not a full TS
  compiler, typechecker, or `ts-node` environment.
- Regex flag behavior follows each runtime: Python helpers use Python `re`
  flags, while JS/TS helpers use JavaScript regex flag strings.

Example JS/TS workflow:

```python
exec_typescript(code=`
const routes: string[] = extract_routes('javascript').map((item) => item.value);
const routeKinds = frequency(
  routes.map((route) => (route.includes('.post(') ? 'write' : 'read')),
  2,
);
const notes = await sub_query_map(
  routes.map((route) => `Explain ${route}`),
  routes,
);
({ routeCount: routes.length, routeKinds, notes })
`, context_id="repo")
```

## Safety Model

Aleph is built to keep raw context out of the model window unless you
explicitly pull it back:

- Tool responses are capped and truncated.
- `get_variable("ctx")` is policy-aware and should not be your default path.
- `exec_python` stdout, stderr, and return values are bounded independently.
- `ALEPH_CONTEXT_POLICY=isolated` adds stricter session export/import rules and
  more defensive defaults.
- `ALEPH_ACTION_POLICY=read-only` (or `--action-policy read-only`) keeps action
  tools in read-only mode: search and file loading still work, but writes and
  subprocess execution are blocked.

The safest pattern is always:

1. Load the large context into Aleph memory.
2. Search or compute inside Aleph.
3. Retrieve only the small result you need.

## Docs Map

- [MCP_SETUP.md](MCP_SETUP.md): client-by-client MCP and skill installation.
- [docs/prompts/aleph.md](docs/prompts/aleph.md): the `/aleph` and `$aleph`
  workflow plus tool patterns.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): flags, env vars, limits, and
  safety settings.
- [docs/langgraph-rlm-default.md](docs/langgraph-rlm-default.md): LangGraph
  integration with Aleph-style tool usage.
- [examples/langgraph_rlm_repo_improver.py](examples/langgraph_rlm_repo_improver.py):
  repo improvement example with optional LangSmith tracing.
- [CHANGELOG.md](CHANGELOG.md): release history.
- [DEVELOPMENT.md](DEVELOPMENT.md): contributor guide.

## Development

```bash
git clone https://github.com/Hmbown/aleph.git
cd aleph
pip install -e ".[dev,mcp]"
# Optional extras:
#   .[docs]           -> MarkItDown-backed document conversion
#   .[observability]  -> OpenTelemetry spans
pytest tests/ -v
ruff check aleph/ tests/
```

## References

- Zhang, A. L., Kraska, T., Khattab, O. (2025)
  [Recursive Language Models (arXiv:2512.24601)](https://arxiv.org/abs/2512.24601)

## License

MIT

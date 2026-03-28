# Aleph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/aleph-rlm.svg)](https://pypi.org/project/aleph-rlm/)

Aleph is an [MCP server](https://modelcontextprotocol.io/) and skill for
**Recursive Language Models** (RLMs). It keeps working state — search indexes,
code execution, evidence, recursion — in a Python process outside the prompt
window, so the LLM reasons iteratively over repos, logs, documents, and data
without burning context on raw content.

```text
+-----------------+    tool calls     +-----------------------------+
|   LLM client    | ---------------> |  Aleph (Python process)     |
| (context budget)| <--------------- |  search / peek / exec / sub |
+-----------------+   small results  +-----------------------------+
```

Why Aleph:

- **Load once, reason many times.** Data lives in Aleph memory, not the prompt.
- **Compute server-side.** `exec_python` runs code over the full context and
  returns only derived results.
- **Recurse.** Sub-queries and recipes split complex work across multiple
  reasoning passes.
- **Persist.** Save sessions and resume long investigations later.

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

## First Workflow

Aleph is best when you load data once, do the heavy work inside Aleph, and only
pull back compact answers.

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
| Large log analysis | Load big files, trace patterns, correlate events |
| Codebase navigation | Search symbols, inspect routes, trace behavior |
| Data exploration | Analyze JSON, CSV, and mixed text with Python helpers |
| Long document review | Load PDFs, Word docs, HTML, and compressed logs |
| Recursive investigations | Split work into sub-queries instead of one giant prompt |
| Long-running sessions | Save and resume memory packs across sessions |

## Core Tools

| Category | Primary tools | What they do |
|---|---|---|
| Load context | `load_context`, `load_file`, `list_contexts`, `diff_contexts` | Put data into Aleph memory and inspect what is loaded |
| Navigate | `search_context`, `semantic_search`, `peek_context`, `chunk_context`, `rg_search` | Find the relevant slice before asking for an answer |
| Compute | `exec_python`, `get_variable` | Run code over the full context and retrieve only the derived result |
| Reason | `think`, `evaluate_progress`, `get_evidence`, `finalize` | Structure progress and close out with evidence |
| Orchestrate | `configure`, `validate_recipe`, `estimate_recipe`, `run_recipe`, `run_recipe_code` | Switch backends and automate repeated reasoning patterns |
| Persist | `save_session`, `load_session` | Keep long investigations outside the prompt window |

Inside `exec_python`, Aleph also exposes helpers such as `search(...)`,
`chunk(...)`, `lines(...)`, `sub_query(...)`, `sub_query_batch(...)`, and
`sub_aleph(...)`. Recursive helpers live inside the REPL, not as top-level MCP
tools.

## Safety Model

Aleph is built to keep raw context out of the model window unless you
explicitly pull it back:

- Tool responses are capped and truncated.
- `get_variable("ctx")` is policy-aware and should not be your default path.
- `exec_python` stdout, stderr, and return values are bounded independently.
- `ALEPH_CONTEXT_POLICY=isolated` adds stricter session export/import rules and
  more defensive defaults.

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
pytest tests/ -v
ruff check aleph/ tests/
```

## References

- Zhang, A. L., Kraska, T., Khattab, O. (2025)
  [Recursive Language Models (arXiv:2512.24601)](https://arxiv.org/abs/2512.24601)

## License

MIT

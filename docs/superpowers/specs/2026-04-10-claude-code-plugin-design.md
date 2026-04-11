# Aleph Claude Code Plugin — Corrected Plugin Design

**Date:** 2026-04-10
**Status:** Draft
**Version target:** 0.9.3 (synced with `pyproject.toml`)

## Overview

Upgrade the existing scaffold at `plugins/aleph/` into a stronger Claude Code
plugin that teaches Aleph's actual RLM workflow instead of presenting Aleph as
"just another file reader." The plugin should expose Aleph's MCP server and
teach the operating discipline that Aleph is built around: keep large contexts
in external memory, navigate with search/peek, compute inside the REPL, recurse
when needed, and only return compact results to the host model.

### Core Principle

**Keep whole contexts out of the prompt. Return only focused slices or compact
derived results.**

That is the truth of Aleph:

- Small raw slices are allowed and useful (`peek_context`, targeted search hits).
- Compact derived values are allowed and useful (`get_variable("summary")`).
- Whole raw contexts are not the workflow.
- `get_variable("ctx")` is blocked by design and should never appear in plugin
  guidance.

## Aleph Truth Constraints

The plugin and its docs must stay aligned with the current repository behavior:

1. The plugin default MCP launch config must match the portable installer
   profile in `aleph/install_config.py`:
   - `aleph`
   - `--enable-actions`
   - `--workspace-mode any`
   - `--tool-docs concise`
2. For large codebases, the default front door is `load_workspace_manifest`,
   not `load_file` on arbitrary source trees.
3. For single large files, the default front door is `load_file` (or
   `load_context` for inline data).
4. `sub_query_map` must be documented with its real signature:
   `sub_query_map(prompts, context_slices=None, limit=None, parallel=True)`.
5. `sub_query_batch` must be documented with its real signature:
   `sub_query_batch(prompt, context_slices, limit=None)`.
6. `sub_aleph` must be documented with its real signature:
   `sub_aleph(query, context=None)`.
7. `exec_python` does **not** return only printed output. In the default
   `output_feedback="full"` mode, Aleph can return stdout, stderr, error text,
   and a rendered return value. The plugin may teach
   `configure(output_feedback="metadata")` when tighter prompt discipline is
   useful, but it must not misstate the runtime behavior.
8. `get_variable("ctx")` is blocked. Plugin guidance should explicitly tell the
   model to retrieve only compact derived values.
9. The plugin wrapper should stay portable by default. Do not pin a nested
   sub-query backend in `.mcp.json`.
10. Do not add speculative plugin-level `userConfig` or `settings.json`
    features unless the Claude Code plugin system is known to support them.

## Scope

**In scope:**
- Claude plugin manifest version sync and metadata refresh
- Codex plugin manifest version sync for consistency across shipped wrappers
- Portable MCP config (`.mcp.json`) using the same defaults as
  `default_mcp_config("portable")`
- `/aleph` skill rewrite grounded in Aleph's real repo/file workflows
- `aleph-expert` agent with the same workflow discipline
- SessionStart install check hook
- README rewrite for installation and correct usage patterns
- `scripts/sync_versions.py` update to sync both plugin manifests

**Out of scope:**
- `/swarm` skill
- Marketplace publishing
- Plugin-level `userConfig`
- `settings.json`
- Pinned nested profiles in the checked-in plugin wrapper

## File Structure

```text
plugins/aleph/
├── .claude-plugin/
│   └── plugin.json
├── .codex-plugin/
│   └── plugin.json
├── .mcp.json
├── skills/
│   └── aleph/
│       └── SKILL.md
├── agents/
│   └── aleph-expert.md
├── hooks/
│   └── hooks.json
├── scripts/
│   └── check-aleph.sh
└── README.md
```

## Component Specifications

### 1. Claude Plugin Manifest (`.claude-plugin/plugin.json`)

Keep this conservative. The checked-in manifest should stay version-synced and
metadata-focused unless the Claude Code plugin schema is explicitly confirmed to
support more fields.

```json
{
  "name": "aleph",
  "version": "0.9.3",
  "description": "Recursive Language Model MCP server for large local data. Keep files, repos, logs, and documents in external memory; search, peek, compute, and recurse without dumping whole contexts into the prompt.",
  "author": {
    "name": "Hunter Bown",
    "url": "https://github.com/Hmbown"
  },
  "homepage": "https://github.com/Hmbown/aleph",
  "repository": "https://github.com/Hmbown/aleph",
  "license": "MIT",
  "keywords": [
    "mcp",
    "rlm",
    "external-memory",
    "search",
    "reasoning",
    "recursive",
    "large-data"
  ]
}
```

**Notes:**
- Version must match `pyproject.toml` and `aleph/__init__.py`.
- Do **not** add speculative `userConfig` here in this iteration.
- Plugin behavior is carried by the checked-in `.mcp.json`, `skills/`,
  `agents/`, and `hooks/` files.

### 2. Shared MCP Config (`.mcp.json`)

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--enable-actions",
        "--workspace-mode",
        "any",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

**Notes:**
- This must match `default_mcp_config("portable")`.
- It intentionally does **not** pin `--sub-query-backend`.
- It intentionally uses a bare `aleph` command rather than a user-specific
  absolute path.
- Do not try to plumb API-backend credentials through the Claude Code plugin.
  Aleph's Claude Code installer already documents that CLI installation does not
  support MCP env injection.

### 3. Skill — `/aleph`

**File:** `skills/aleph/SKILL.md`

The skill should teach a 5-phase loop grounded in Aleph's actual tooling.

#### Phase 1: Load

Pick the right front door:

- **Large repo / codebase**: start with `load_workspace_manifest(...)`
- **Single large file**: use `load_file(...)`
- **Inline / dynamic content**: use `load_context(...)`

For repo work, the default pattern should look like:

```text
load_workspace_manifest(paths=["src", "tests"], context_id="repo")
rg_search(pattern="FastAPI|APIRouter|router\\.", paths=["src", "tests"], load_context_id="routes")
load_file(path="pyproject.toml", context_id="pyproject")
```

#### Phase 2: Orient

- Use `search_context` to find relevant regions
- Use `peek_context` to inspect small ranges
- Use `semantic_search` for meaning-based lookup
- Use `chunk_context` when navigability matters
- Do not pull entire contexts into the prompt

#### Phase 3: Compute

- Use `exec_python` for heavy analysis with `ctx` bound in the sandbox
- Use built-in helpers like `search`, `peek`, `lines`, `chunk`,
  `extract_*`, `semantic_search`, and `cite`
- Use `get_variable` only for small derived values like `summary`, `result`,
  `counts`, or `matches`
- Explicitly state that `get_variable("ctx")` is blocked
- Mention that Aleph defaults to `output_feedback="full"`, and that
  `configure(output_feedback="metadata")` is useful when output volume becomes
  distracting

#### Phase 4: Recurse

Document the real helper signatures:

- `sub_query(prompt, context_slice=None)`
- `sub_query_batch(prompt, context_slices, limit=None)`
- `sub_query_map(prompts, context_slices=None, limit=None, parallel=True)`
- `sub_aleph(query, context=None)`

Runtime guidance:

- Use `configure(sub_query_share_session=true)` when nested agents need access
  to parent contexts
- For depth 3+, recommend
  `configure(sub_query_timeout=300, sandbox_timeout=300)`

#### Phase 5: Converge

- Use `evaluate_progress` when the answer is not yet stable
- Loop back through orient/compute when confidence is low
- Use `summarize_so_far` if the trajectory is getting long
- Use `finalize(answer=..., confidence=..., context_id=...)` when done

#### Depth Invocation Table

| Invocation | Depth | Strategy |
|-----------|-------|----------|
| `/aleph file.py` | 1 | Direct file analysis with `load_file`, `search_context`, `peek_context`, `exec_python` |
| `/aleph repo/` | 1 | Repo analysis with `load_workspace_manifest`, `rg_search`, targeted `load_file`, `exec_python` |
| `/aleph 2 file.py` | 2 | Fan-out with `sub_query_batch` or `sub_query_map` |
| `/aleph 3 file.py` | 3 | Recursive `sub_aleph` with longer timeouts |
| `/aleph 4 file.py` | 4 | Deep recursion with explicit timeout tuning |

#### Anti-Patterns

The skill should call these out explicitly:

- Using `read_file` as the default entry point for large files or repos
- Loading a whole repo file-by-file when `load_workspace_manifest` is the
  better entry point
- Treating `get_variable("ctx")` as a valid workflow
- Pasting raw content into the prompt when Aleph can search or compute instead
- Claiming that only `print()` output comes back from `exec_python`
- Pinning API-mode nested backends in the plugin wrapper without env support

### 4. Agent — `aleph-expert`

**File:** `agents/aleph-expert.md`

Purpose: a subagent that follows the same RLM discipline as the skill.

It should:

- Prefer `load_workspace_manifest` for repo-scale analysis
- Prefer `load_file` for single large files
- Search before peeking
- Compute in `exec_python`
- Retrieve only compact derived variables
- Use the real recursion helper signatures
- Mention `output_feedback="metadata"` as an optional optimization, not as the
  default runtime behavior
- Explicitly avoid `get_variable("ctx")`

### 5. Hook — SessionStart Install Check

**Files:**
- `hooks/hooks.json`
- `scripts/check-aleph.sh`

Behavior:

- If `aleph` is missing from `PATH`, print install guidance and exit 0
- If present, print the imported package version using:

```bash
python3 -c "import aleph; print(aleph.__version__)"
```

Constraints:

- Informational only
- Never block Claude Code startup
- Use a plugin-relative path from the hook file to the script

### 6. README

Update `plugins/aleph/README.md` to cover:

- What the plugin includes
- Prerequisites: `pip install "aleph-rlm[mcp]"`
- The portable default wrapper behavior
- The difference between repo workflow and single-file workflow
- The fact that `load_workspace_manifest` is the default front door for repos
- The fact that `get_variable("ctx")` is blocked
- The fact that `.mcp.json` intentionally does not pin a nested backend
- How users can choose a pinned backend later:
  - via `aleph-rlm install claude-code --profile claude`
  - via runtime `configure(...)`

## Version Sync Strategy

The plugin version must stay in sync with:

- `pyproject.toml`
- `aleph/__init__.py`
- `plugins/aleph/.claude-plugin/plugin.json`
- `plugins/aleph/.codex-plugin/plugin.json`

Update `scripts/sync_versions.py` accordingly.

## Migration from Current State

1. Update both plugin manifests from `0.8.9` to `0.9.3`
2. Keep `.mcp.json` portable with bare `aleph`
3. Rewrite `skills/aleph/SKILL.md` around the corrected workflow
4. Add `agents/aleph-expert.md`
5. Add `hooks/hooks.json`
6. Add `scripts/check-aleph.sh`
7. Rewrite `plugins/aleph/README.md`
8. Extend `scripts/sync_versions.py` to sync both manifests

## Testing

- `claude --plugin-dir ./plugins/aleph` loads successfully
- MCP tools are available (`list_contexts` works)
- The hook prints the Aleph version when installed
- The hook prints install guidance when `aleph` is absent
- `/aleph /absolute/path/to/file.log` teaches the single-file workflow
- `/aleph repo/` or repo-oriented requests teach the `load_workspace_manifest`
  workflow
- `scripts/sync_versions.py --check` validates version sync

## Implementation Notes

If implementation reveals Claude Code plugin-schema constraints that are not yet
captured in this repo, prefer the smallest change that preserves Aleph truth:

- Keep the metadata manifest minimal
- Keep `.mcp.json` static and portable
- Put workflow intelligence into the skill, agent, README, and hook

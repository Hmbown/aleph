# Aleph Plugin

This directory packages the Aleph MCP server as a plugin for both **Claude
Code** and **Codex**.

Aleph keeps large files, repos, logs, and documents in external memory so the
host model can search, peek, compute, and recurse without dumping whole
contexts into the prompt.

## What It Includes

- `.claude-plugin/plugin.json`: Claude Code plugin manifest
- `.codex-plugin/plugin.json`: Codex plugin manifest
- `.mcp.json`: Aleph MCP server launch configuration (shared by both)
- `skills/aleph/SKILL.md`: Aleph workflow guidance
- `agents/aleph-expert.md`: Aleph-focused subagent
- `hooks/hooks.json`: non-blocking Claude SessionStart hook
- `scripts/check-aleph.sh`: install/version check used by the hook

## Prerequisites

```bash
pip install "aleph-rlm[mcp]"
```

The `aleph` executable must then be available on `PATH`.

## Portable Default Wrapper

The checked-in `.mcp.json` matches Aleph's portable installer defaults:

- `--enable-actions` (filesystem/shell tools)
- `--workspace-mode any`
- `--tool-docs concise`

The wrapper intentionally:

- uses a bare `aleph` command rather than a user-specific path
- does not add plugin-level `userConfig`
- does not ship `settings.json`
- does not pin a nested `--sub-query-backend`

That keeps the plugin portable across shared repos and different host clients.

## Workflow Defaults

### Repo or codebase workflow

For repo-scale analysis, the default front door is
`load_workspace_manifest(...)`.

Typical pattern:

- `load_workspace_manifest(...)`
- `rg_search(...)`
- targeted `load_file(...)`
- `search_context(...)`, `peek_context(...)`, and `exec_python(...)`

### Single-file workflow

For one large file, the default front door is `load_file(...)`.

Use `load_context(...)` only for inline or generated content.

### Compute and retrieval rules

- Use `exec_python(...)` for heavy analysis inside Aleph
- `exec_python(...)` is not print-only; default `output_feedback="full"` can
  return stdout, stderr, errors, and a rendered return value
- Optionally use `configure(output_feedback="metadata")` when you want tighter
  prompt discipline
- Retrieve only compact derived values via `get_variable(...)`
- Treat `get_variable("ctx")` as blocked workflow for this plugin; use bounded
  slices or derived variables instead

## Nested Backends

The shared plugin wrapper intentionally does not pin a nested backend.

If you want one later, choose it explicitly:

```bash
aleph-rlm install claude-code --profile claude
```

Or configure it at runtime:

```text
configure(sub_query_backend="claude")
configure(sub_query_share_session=true)
```

The checked-in wrapper stays portable by default.

## Claude Code Installation

```bash
# From the repo root — add as a local marketplace, then install
claude plugin marketplace add ./plugins/aleph
claude plugin install aleph

# Or load directly for a single session
claude --plugin-dir ./plugins/aleph
```

The bundled SessionStart hook is informational only. It prints the installed
Aleph version when available and prints install guidance when `aleph` is
missing from `PATH`.

## Codex Installation

The Codex plugin scaffold is in `.codex-plugin/`. Codex plugin discovery is
experimental — see the Codex plugin docs for current status.

# Aleph Plugin

This directory packages the Aleph MCP server as a plugin for both **Claude Code**
and **Codex**.

## What It Includes

- `.claude-plugin/plugin.json`: Claude Code plugin manifest
- `.codex-plugin/plugin.json`: Codex plugin manifest
- `.mcp.json`: Aleph MCP server launch configuration (shared by both)
- `skills/aleph/SKILL.md`: Aleph workflow guidance

## Prerequisites

- `aleph-rlm[mcp]` is installed in the active Python environment
- `aleph` is available on `PATH`

## MCP Configuration

The bundled `.mcp.json` launches Aleph with:

- `--enable-actions` (filesystem/shell tools)
- `--workspace-mode any`
- `--tool-docs concise`

This plugin intentionally uses the `portable` profile by default: it does not
pin a nested sub-query backend. That keeps the plugin config minimal and avoids
embedding client-specific nested-agent assumptions into the shared wrapper.

If you want a pinned nested profile, prefer the installer:

```bash
aleph-rlm install --profile claude
# or
aleph-rlm install --profile codex
```

The `claude` profile pins Claude sub-queries with `--model opus` and
`--effort low`. The `codex` profile pins Codex MCP sub-queries with low
reasoning effort and shared-session enabled.

## Claude Code Installation

```bash
# From the repo root — add as a local marketplace, then install
claude plugin marketplace add ./plugins/aleph
claude plugin install aleph

# Or load directly for a single session
claude --plugin-dir ./plugins/aleph
```

## Codex Installation

The Codex plugin scaffold is in `.codex-plugin/`. Codex plugin discovery is
experimental — see the Codex plugin docs for current status.

## Status

Both plugin surfaces are functional. The Claude Code plugin system is the
primary supported path.

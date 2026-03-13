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
- `ALEPH_SUB_QUERY_SHARE_SESSION=true`

Aleph auto-detects the sub-query backend (`claude`, `codex`, `gemini`, etc.)
based on which CLI is available. No manual backend config is needed.

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

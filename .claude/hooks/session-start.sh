#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install project with dev and mcp extras
pip install -e ".[dev,mcp]"

# Install ruff for linting (used in dev workflow but not in dev deps)
pip install ruff

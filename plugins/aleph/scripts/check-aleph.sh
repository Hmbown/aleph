#!/usr/bin/env bash

set -u

if ! command -v aleph >/dev/null 2>&1; then
  cat <<'EOF'
[Aleph plugin] `aleph` is not on PATH.
Install it with:
  pip install "aleph-rlm[mcp]"
Then restart Claude Code or re-enable the plugin.
EOF
  exit 0
fi

if version=$(python3 -c "import aleph; print(aleph.__version__)" 2>/dev/null); then
  printf '[Aleph plugin] aleph-rlm %s available on PATH.\n' "$version"
else
  cat <<'EOF'
[Aleph plugin] `aleph` is on PATH, but `python3 -c "import aleph; print(aleph.__version__)"` failed.
Check that the active Python environment has `aleph-rlm[mcp]` installed.
EOF
fi

exit 0

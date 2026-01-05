# OpenAI Codex and ChatGPT setup

Use this checklist to connect Aleph's MCP server to OpenAI clients that can launch local MCP servers.

## Codex CLI

Preferred path (auto-install):
```bash
pip install aleph-rlm[mcp]
aleph-rlm install codex
```

Manual config (TOML) in `~/.codex/config.toml`:
```toml
[mcp_servers.aleph]
command = "aleph-mcp-local"
args = []
```

Restart Codex CLI after changes.

## Codex Skills

Codex skills live in `~/.codex/skills`. To install the `/aleph` skill from this repo:
```bash
mkdir -p ~/.codex/skills/aleph
cp /path/to/aleph/ALEPH.md ~/.codex/skills/aleph/SKILL.md
```

Restart Codex CLI after changes.

## ChatGPT / OpenAI desktop clients

If your client exposes MCP server settings, add a server with:
- Name: `aleph`
- Command: `aleph-mcp-local`
- Args: `[]`

Notes:
- The client must run on the same machine where `aleph-mcp-local` is installed.
- Verify installation with `aleph-rlm doctor`.

## Troubleshooting

- If `aleph-mcp-local` is not found, reinstall: `pip install aleph-rlm[mcp]`.
- If you need a provider-backed MCP server (non-local), use `aleph-mcp` and pass
  provider/model flags in your MCP client command.

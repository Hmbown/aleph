# OpenAI Codex and ChatGPT setup

Use this checklist to connect Aleph's MCP server to OpenAI clients that can launch local MCP servers.

## Codex CLI

Preferred path (auto-install):
```bash
pip install "aleph-rlm[mcp]"
aleph-rlm install codex
```

Manual config in your Codex config file:
- **macOS/Linux:** `~/.codex/config.toml`
- **Windows:** `%USERPROFILE%\.codex\config.toml`
```toml
[mcp_servers.aleph]
command = "aleph"
args = ["--enable-actions", "--tool-docs", "concise"]

[mcp_servers.aleph.env]
ALEPH_SUB_QUERY_BACKEND = "codex"
ALEPH_SUB_QUERY_CODEX_MODE = "mcp"
ALEPH_SUB_QUERY_CODEX_MODEL = "gpt-5.4"
ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT = "low"
ALEPH_SUB_QUERY_SHARE_SESSION = "true"
```

Restart Codex CLI after changes.

## Codex Skills

Install the `$aleph` skill:

**Option 1:** Download [`docs/prompts/aleph.md`](../prompts/aleph.md) and save to:
- macOS/Linux: `~/.codex/skills/aleph/SKILL.md`
- Windows: `%USERPROFILE%\.codex\skills\aleph\SKILL.md`

**Option 2:** From installed package:

<details>
<summary>macOS/Linux</summary>

```bash
mkdir -p ~/.codex/skills/aleph
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" ~/.codex/skills/aleph/SKILL.md
```
</details>

<details>
<summary>Windows (PowerShell)</summary>

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills\aleph"
$alephPath = python -c "import aleph; print(aleph.__path__[0])"
Copy-Item "$alephPath\..\docs\prompts\aleph.md" "$env:USERPROFILE\.codex\skills\aleph\SKILL.md"
```
</details>

Restart Codex CLI after changes.

## ChatGPT / OpenAI desktop clients

If your client exposes MCP server settings, add a server with:
- Name: `aleph`
- Command: `aleph`
- Args: `["--enable-actions", "--tool-docs", "concise"]`

Notes:
- The client must run on the same machine where `aleph` is installed.
- Verify installation with `aleph-rlm doctor`.

## Troubleshooting

- If `aleph` is not found, reinstall: `pip install "aleph-rlm[mcp]"`.

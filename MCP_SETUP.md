# MCP Server Configuration Guide

How to configure Aleph as an MCP server in **all major MCP-compatible clients**:
Cursor, VS Code, Claude Desktop, Codex CLI, Windsurf, Kimi CLI, and others.

---

## Quick Start (Full Power Mode)

For maximum capability without needing to configure workspace roots:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise"]
    }
  }
}
```

This enables:

- **All action tools** (`read_file`, `write_file`, `run_command`, `run_tests`)
- **Any git repo access** (not limited to a single workspace root)
- **Concise tool descriptions** (cleaner MCP tool list)

## Recommended Nested Setup

The installer now asks for a sub-query profile up front:

```bash
aleph-rlm install
aleph-rlm install --profile claude
aleph-rlm install --profile codex
aleph-rlm install --profile portable
```

Recommended profiles:

1. `claude` for Claude-first installs: backend `claude`, model `opus`, effort `low`
2. `codex` for the strongest validated shared-session path
3. `portable` when you do not want to pin the nested backend yet

---

## Shared Sub-Query Sessions (Live Sandbox)

If you want CLI sub-agents spawned via `sub_query` to access the **same live
Aleph session** (tools, contexts, and sandbox state), enable streamable HTTP
sharing:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--enable-actions",
        "--workspace-mode", "any",
        "--tool-docs", "concise",
        "--sub-query-backend", "codex",
        "--sub-query-share-session", "true",
        "--sub-query-timeout", "300",
        "--sub-query-codex-mode", "mcp",
        "--sub-query-codex-model", "gpt-5.4",
        "--sub-query-codex-reasoning-effort", "low"
      ]
    }
  }
}
```

Notes:

- The Aleph server spins up a **local streamable HTTP endpoint** on demand.
- The nested CLI is pointed at that live server automatically.
- With `ALEPH_SUB_QUERY_CODEX_MODE=mcp`, Aleph talks to `codex mcp-server`
  instead of `codex exec`, and Codex receives the live Aleph server as a nested
  MCP server.
- Claude receives the shared session through `--mcp-config` and
  `--strict-mcp-config`, which keeps the nested run isolated from unrelated
  Claude MCP config.
- Gemini receives the shared session through a temp settings file via
  `GEMINI_CLI_SYSTEM_SETTINGS_PATH`; this works, but it is still the noisier
  experimental path.
- Customize host/path with `ALEPH_SUB_QUERY_HTTP_HOST` and
  `ALEPH_SUB_QUERY_HTTP_PATH` if needed.
- Tools are exposed under the server name you choose (default: `aleph_shared`).
- `aleph_shared` avoids conflicts with an existing `aleph` stdio entry in Codex
  config.

For even higher limits:

```json
{
  "args": [
    "--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise",
    "--timeout", "120", "--max-output", "100000"
  ]
}
```

---

## Per-Client Configuration

Every client below uses the same core args. Only the file location and format
differ. Replace `/path/to/your-project` with your actual project root.

### Cursor

Cursor loads MCP servers from JSON. **Chat, Composer, and the Cursor CLI agent**
use the same MCP list for a workspace; you do not need a separate VS Code–style
extension unless you are shipping a marketplace extension that registers a
server via `vscode.cursor.mcp.registerServer` ([MCP extension API](https://docs.cursor.com/context/mcp-extension-api)).
For Aleph, **stdio MCP via `mcp.json` is sufficient**.

**Scopes**

| Scope | Path | Typical use |
|-------|------|-------------|
| Global | macOS/Linux `~/.cursor/mcp.json`, Windows `%USERPROFILE%\.cursor\mcp.json` | Same tools in every folder |
| Project | `<repo>/.cursor/mcp.json` | Team-shared, per-repo roots |

**Installer**

```bash
# Global (~/.cursor/mcp.json): broad workspace (--workspace-mode any)
aleph-rlm install cursor --profile portable

# Project (.cursor/mcp.json in cwd): fixed root = opened folder
cd /path/to/your-repo
aleph-rlm install cursor-project --profile portable
```

`aleph-rlm install cursor-project` writes `"type": "stdio"`, `--workspace-root`
`${workspaceFolder}`, and `--workspace-mode fixed` so action tools stay scoped
to the workspace Cursor opened ([MCP variables](https://docs.cursor.com/context/mcp)).

**Global example** (multi-repo / no single root; same as generic quick start):

```json
{
  "mcpServers": {
    "aleph": {
      "type": "stdio",
      "command": "aleph",
      "args": [
        "--enable-actions",
        "--workspace-mode", "any",
        "--tool-docs", "concise"
      ]
    }
  }
}
```

**Project example** (recommended when you commit `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "aleph": {
      "type": "stdio",
      "command": "aleph",
      "args": [
        "--enable-actions",
        "--workspace-root", "${workspaceFolder}",
        "--workspace-mode", "fixed",
        "--tool-docs", "concise"
      ]
    }
  }
}
```

Optional: set `"env": { "PYTHONPATH": "${workspaceFolder}" }` only if you are
developing Aleph from source inside the repo and need local imports; omit for
normal `pip install` setups.

**stdio transport:** Cursor expects the server to speak MCP over stdin/stdout
(no TTY). Run `aleph` (or `python -m aleph.mcp.local_server`) with no extra
wrappers that attach a terminal to the child process.

### VS Code

Config file:

- **macOS / Linux:** `~/.vscode/mcp.json`
- **Windows:** `%USERPROFILE%\.vscode\mcp.json`

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root", "/path/to/your-project",
        "--enable-actions",
        "--tool-docs", "concise"
      ]
    }
  }
}
```

### Claude Code (CLI)

Config file:

- **macOS / Linux:** `~/.claude/settings.json`
- **Windows:** `%USERPROFILE%\.claude\settings.json`

**Auto-discovery (simplest):**

```bash
aleph-rlm install claude-code
# or with a sub-query profile
aleph-rlm install claude-code --profile claude
```

Then restart Claude Code (`/mcp` to verify).

**Manual:**

Add to the `mcpServers` key in `~/.claude/settings.json`:

```json
{
  "aleph": {
    "command": "aleph",
    "args": [
      "--enable-actions",
      "--workspace-mode", "any",
      "--tool-docs", "concise"
    ]
  }
}
```

<details>
<summary><strong>Installing the Claude Code skill</strong></summary>

**Option 1:** Download [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and
save to:

- macOS / Linux: `~/.claude/commands/aleph.md`
- Windows: `%USERPROFILE%\.claude\commands\aleph.md`

**Option 2:** From installed package:

```bash
# macOS / Linux
mkdir -p ~/.claude/commands
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" \
  ~/.claude/commands/aleph.md
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\commands"
$alephPath = python -c "import aleph; print(aleph.__path__[0])"
Copy-Item "$alephPath\..\docs\prompts\aleph.md" "$env:USERPROFILE\.claude\commands\aleph.md"
```

This enables the `/aleph` command for structured reasoning workflows.

</details>

### Claude Desktop

Config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--enable-actions",
        "--workspace-mode", "any",
        "--tool-docs", "concise"
      ]
    }
  }
}
```

### Codex CLI

Config file:

- **macOS / Linux:** `~/.codex/config.toml`
- **Windows:** `%USERPROFILE%\.codex\config.toml`

```toml
[mcp_servers.aleph]
command = "aleph"
args = ["--enable-actions", "--tool-docs", "concise"]
```

With a fixed workspace root:

```toml
[mcp_servers.aleph]
command = "aleph"
args = [
  "--workspace-root", "/path/to/your-project",
  "--enable-actions",
  "--tool-docs", "concise"
]
```

<details>
<summary><strong>Installing the Codex skill</strong></summary>

**Option 1:** Download [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and
save to:

- macOS / Linux: `~/.codex/skills/aleph/SKILL.md`
- Windows: `%USERPROFILE%\.codex\skills\aleph\SKILL.md`

**Option 2:** From installed package:

```bash
# macOS / Linux
mkdir -p ~/.codex/skills/aleph
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" \
  ~/.codex/skills/aleph/SKILL.md
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills\aleph"
$alephPath = python -c "import aleph; print(aleph.__path__[0])"
Copy-Item "$alephPath\..\docs\prompts\aleph.md" "$env:USERPROFILE\.codex\skills\aleph\SKILL.md"
```

This enables the `$aleph` command in Codex.

</details>

### Kimi CLI

Add via the command line:

```bash
kimi mcp add --transport stdio aleph -- \
  aleph --enable-actions --tool-docs concise --workspace-root /path/to/your-project
```

Or edit `~/.kimi/mcp.json` directly:

```json
{
  "mcpServers": {
    "aleph": {
      "transport": "stdio",
      "command": "aleph",
      "args": [
        "--enable-actions", "--tool-docs", "concise",
        "--workspace-root", "/path/to/your-project"
      ]
    }
  }
}
```

<details>
<summary><strong>Installing the Kimi skill</strong></summary>

Kimi CLI searches for skills in these locations (in order):

1. `~/.config/agents/skills/`
2. `.agents/skills/` (project root)
3. `~/.agents/skills/`
4. `~/.kimi/skills/`
5. `~/.claude/skills/`
6. `~/.codex/skills/`
7. `.kimi/skills/` (project root)
8. `.claude/skills/` (project root)
9. `.codex/skills/` (project root)

**User-level (recommended):**

```bash
mkdir -p ~/.config/agents/skills/aleph
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" \
  ~/.config/agents/skills/aleph/SKILL.md
```

**Project-level:**

```bash
mkdir -p .agents/skills/aleph
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" \
  .agents/skills/aleph/SKILL.md
```

Override the search path with `--skills-dir`.

</details>

### Windsurf

Standard MCP configuration:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root", "/path/to/your-project",
        "--enable-actions",
        "--tool-docs", "concise"
      ]
    }
  }
}
```

### Cline / Continue.dev

These clients support standard MCP configuration. Check their documentation for
exact file locations and format.

### Generic MCP Client

Key parameters:

| Parameter              | Value                                            |
|------------------------|--------------------------------------------------|
| Command                | `aleph`                                          |
| Required args          | `--workspace-root /path/to/project`              |
| Optional args          | `--enable-actions`, `--tool-docs concise`, `--require-confirmation`, `--timeout N` |

---

## Parameters Reference

| Flag                                 | Default          | Description                                         |
|--------------------------------------|------------------|-----------------------------------------------------|
| `--workspace-root <path>`            | auto-detect      | Root directory for file operations                   |
| `--workspace-mode <fixed\|git\|any>` | `fixed`          | Path scope: single dir, any git repo, or unrestricted|
| `--enable-actions`                   | off              | Enable action tools (read/write/run)                 |
| `--require-confirmation`             | off              | Require `confirm=true` on action calls               |
| `--tool-docs <concise\|full>`        | `concise`        | Tool description verbosity                           |
| `--timeout <seconds>`                | 60               | Sandbox execution timeout                            |
| `--max-output <chars>`               | 50,000           | Max output characters from commands                  |
| `--max-file-size <bytes>`            | 1,000,000,000    | Max file size for read operations (1 GB)             |
| `--max-write-bytes <bytes>`          | 100,000,000      | Max file size for write operations (100 MB)          |

---

## Sub-Query Backends

`sub_query` can use an API backend or a local CLI backend. When
`ALEPH_SUB_QUERY_BACKEND` is `auto` (default), Aleph chooses the first
available:

1. **codex CLI** -- if installed
2. **API** -- if API credentials are available (fallback)

`claude`, `gemini`, and `kimi` remain available only when explicitly selected.

Practical guidance:

- **Use Codex** for the default nested/shared-session path
- **Use Claude** when you explicitly want all-Claude operation
- **Use Gemini** only as an explicit experimental override
- **Use API** when CLI backends are unavailable or you need an OpenAI-compatible endpoint

### API Configuration

The API backend uses **OpenAI-compatible endpoints only**:

| Variable                  | Fallback          | Description                                   |
|---------------------------|-------------------|-----------------------------------------------|
| `ALEPH_SUB_QUERY_API_KEY` | `OPENAI_API_KEY` | API key                                       |
| `ALEPH_SUB_QUERY_URL`     | `OPENAI_BASE_URL`| Base URL (default: `https://api.openai.com/v1`)|
| `ALEPH_SUB_QUERY_MODEL`   | --                | Model name (**required**)                     |

### Quick Setup Examples

```bash
# OpenAI
export ALEPH_SUB_QUERY_API_KEY=sk-...
export ALEPH_SUB_QUERY_MODEL=your-model-name

# Groq (fast inference)
export ALEPH_SUB_QUERY_API_KEY=gsk_...
export ALEPH_SUB_QUERY_URL=https://api.groq.com/openai/v1
export ALEPH_SUB_QUERY_MODEL=llama-3.3-70b-versatile

# Local LLM (Ollama, LM Studio, etc.)
# Make sure your local server is running and the model is available.
export ALEPH_SUB_QUERY_API_KEY=ollama   # any non-empty value
export ALEPH_SUB_QUERY_URL=http://localhost:11434/v1
export ALEPH_SUB_QUERY_MODEL=llama3.2
```

### All Sub-Query Variables

| Variable                          | Description                                                    |
|-----------------------------------|----------------------------------------------------------------|
| `ALEPH_SUB_QUERY_BACKEND`        | Force backend: `api`, `claude`, `codex`, `gemini`, `kimi`, `auto` |
| `ALEPH_SUB_QUERY_SHARE_SESSION`  | Share the live Aleph MCP session with nested CLI sub-agents    |
| `ALEPH_SUB_QUERY_CLAUDE_MODEL`   | Claude CLI model alias/name (default: `opus`)                  |
| `ALEPH_SUB_QUERY_CLAUDE_EFFORT`  | Claude CLI effort (default: `low`)                             |
| `ALEPH_SUB_QUERY_CODEX_MODE`     | Codex mode: `mcp` (default) or `exec`                          |
| `ALEPH_SUB_QUERY_CODEX_MODEL`    | Codex MCP model override (default: `gpt-5.4`)                  |
| `ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT` | Codex MCP reasoning effort (default: `low`)          |
| `ALEPH_SUB_QUERY_API_KEY`        | API key (fallback: `OPENAI_API_KEY`)                           |
| `ALEPH_SUB_QUERY_URL`            | API base URL (fallback: `OPENAI_BASE_URL`)                     |
| `ALEPH_SUB_QUERY_MODEL`          | Model name (required for API backend)                          |

Use `ALEPH_SUB_QUERY_BACKEND` to pin a backend or bypass auto-detection;
otherwise leave it unset for the default `auto` selection order.

Runtime `configure(sub_query_backend=...)` overrides auto-detection for the
active server session. The install/configure wizard now makes the nested
backend an explicit profile choice instead of silently pinning Codex.

> **Note:** Some MCP clients don't reliably pass `env` vars from their config to
> the server process. If `sub_query` reports "API key not found" despite your
> client's MCP settings, add the exports to your shell profile:
>
> - **macOS / Linux:** `~/.zshrc` or `~/.bashrc`
> - **Windows:** System Environment Variables or `$PROFILE` in PowerShell
>
> Then restart your terminal/client.

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full list.

---

## Workspace Root

The workspace root should be the directory containing your `.git` folder,
`pyproject.toml`, `package.json`, etc.

**Automatic detection:** if you don't set `--workspace-root`, Aleph will:

1. Use `ALEPH_WORKSPACE_ROOT` if set
2. Prefer `PWD` (falls back to `INIT_CWD`) when present
3. Check if `.git` exists in that directory
4. If not, search parent directories until finding `.git`
5. Use that directory as the workspace root

**Recommended:** always set `--workspace-root` explicitly to avoid ambiguity.
For multi-repo work, prefer `--workspace-mode git` and use absolute paths (or a
broad workspace root).

---

## Example Scenarios

### Python Project

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root", "/Users/yourname/projects/my-python-app",
        "--enable-actions",
        "--tool-docs", "concise"
      ]
    }
  }
}
```

### Monorepo (scoped to a subdirectory)

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root", "/Users/yourname/monorepo/packages/frontend",
        "--enable-actions",
        "--tool-docs", "concise"
      ]
    }
  }
}
```

### Any Git Repo

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--enable-actions",
        "--tool-docs", "concise",
        "--workspace-mode", "git"
      ]
    }
  }
}
```

### Increased Limits

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root", "/path/to/project",
        "--enable-actions",
        "--tool-docs", "concise",
        "--timeout", "60",
        "--max-output", "100000",
        "--max-file-size", "5000000000",
        "--max-write-bytes", "500000000"
      ]
    }
  }
}
```

---

## Security

### Actions Mode

When you enable `--enable-actions`, you grant Aleph permission to:

| Capability        | Default Limit |
|-------------------|---------------|
| **Read files**    | Up to 1 GB    |
| **Write files**   | Up to 100 MB  |
| **Run commands**  | 60 s timeout  |
| **Run tests**     | 60 s timeout  |

Use `--workspace-mode git` to limit access to git repos, or
`--workspace-mode any` to remove path restrictions.

### Confirmation Mode

Use `--require-confirmation` for safer operation. When enabled, all action tools
require `confirm=true` in the call:

```json
{
  "args": [
    "--workspace-root", "/path/to/project",
    "--enable-actions",
    "--tool-docs", "concise",
    "--require-confirmation"
  ]
}
```

---

## Troubleshooting

### "Path escapes workspace root"

**Cause:** workspace root not set or incorrect.

**Fix:** add `--workspace-root` with the correct path, or use
`--workspace-mode git` / `--workspace-mode any` for multi-repo access.

### "Actions are disabled"

**Cause:** `--enable-actions` flag not set.

**Fix:** add `--enable-actions` to the `args` array in your MCP config.

### MCP Server Not Starting

Check in order:

1. Aleph is installed: `pip install "aleph-rlm[mcp]"`
2. Entry point works: `aleph --help`
3. Python is in PATH: try the full path to `python3`
4. Workspace root path is correct
5. MCP client was restarted after config changes

### sub_query Timed Out

Increase the timeout and/or reduce context size:

```bash
# Environment variable
export ALEPH_SUB_QUERY_TIMEOUT=120

# CLI flag
aleph --sub-query-timeout 120
```

**Runtime (MCP tool):**

```python
mcp__aleph__configure(sub_query_timeout=120)
```

For very large context slices, chunk first (e.g., `chunk(100000)` +
`sub_query_batch`).

### sub_query Reports "API Key Not Found"

**Cause:** some MCP clients don't pass `env` vars reliably.

**Fix:** add credentials to your shell profile:

```bash
# macOS / Linux (~/.zshrc or ~/.bashrc)
export ALEPH_SUB_QUERY_API_KEY=sk-...
export ALEPH_SUB_QUERY_MODEL=your-model-name
```

```powershell
# Windows (PowerShell $PROFILE)
$env:ALEPH_SUB_QUERY_API_KEY = "sk-..."
$env:ALEPH_SUB_QUERY_MODEL = "your-model-name"
```

Then restart your terminal/MCP client.

---

## Related Documentation

| Document                                                | Description                        |
|---------------------------------------------------------|------------------------------------|
| [README.md](README.md)                                  | Project overview and installation  |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md)          | Full configuration reference       |
| [DEVELOPMENT.md](DEVELOPMENT.md)                        | Architecture and contributing      |

---

## Support

For MCP configuration issues, check:

1. Your MCP client documentation (Cursor, VS Code, Claude Desktop, Codex, etc.)
2. This configuration guide
3. [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

For Aleph-specific bugs or feature requests, open an issue on
[GitHub](https://github.com/Hmbown/aleph).

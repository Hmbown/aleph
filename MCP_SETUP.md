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

For the simplest validated setup:

1. Install the Codex CLI
2. Run `aleph-rlm install`
3. Restart your MCP client

That is the recommended path even if your top-level client is Claude Code.
Aleph can keep Claude as the outer client and still use Codex for nested
`sub_query` work, which is currently the cleanest and most reliable shared-
session backend.

If you want an all-Claude setup instead, keep Aleph installed normally and add:

```bash
export ALEPH_SUB_QUERY_BACKEND=claude
export ALEPH_SUB_QUERY_SHARE_SESSION=true
```

Claude works as an explicit fallback, but Codex is the better default for
nested MCP/shared-session recursion.

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
      "args": ["--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise"],
      "env": {
        "ALEPH_SUB_QUERY_SHARE_SESSION": "true",
        "ALEPH_SUB_QUERY_BACKEND": "codex",
        "ALEPH_SUB_QUERY_CODEX_MODE": "mcp",
        "ALEPH_SUB_QUERY_CODEX_MODEL": "gpt-5.4",
        "ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT": "low",
        "ALEPH_SUB_QUERY_HTTP_PORT": "8765",
        "ALEPH_SUB_QUERY_MCP_SERVER_NAME": "aleph_shared"
      }
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
- Runtime default remains `ALEPH_SUB_QUERY_SHARE_SESSION=false`, but generated
  Codex configs pin it to `true` because that is the validated best default for
  nested Codex MCP use.

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

Config file:

- **macOS / Linux:** `~/.cursor/mcp.json`
- **Windows:** `%USERPROFILE%\.cursor\mcp.json`

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

### Claude Desktop

Config file:

- **macOS / Linux:** `~/.claude/settings.json`
- **Windows:** `%USERPROFILE%\.claude\settings.json`

**Auto-discovery (simplest):**

```bash
aleph-rlm install claude-code
```

Then restart Claude Desktop.

**Manual:**

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

<details>
<summary><strong>Installing the Claude Desktop skill</strong></summary>

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

1. **API** -- if API credentials are available (preferred)
2. **codex CLI** -- if installed (fallback)

`claude`, `gemini`, `kimi`, and `opencode` remain available only when explicitly selected.

Practical guidance:

- **Use API** as the default when you have credentials for any OpenAI-compatible endpoint
- **Use Codex** for nested/shared-session paths or when API credentials are unavailable
- **Use Claude** when you explicitly want all-Claude operation
- **Use Gemini** only as an explicit experimental override
- **Use OpenCode** for GLM-series models via the OpenCode CLI

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

# OpenCode CLI (glm-5-turbo default)
export ALEPH_SUB_QUERY_BACKEND=opencode
# Optionally override model:
export ALEPH_SUB_QUERY_OPENCODE_MODEL=glm-5-turbo
```

### CLIProxyAPI

`vendor/cliproxyapi` provides a unified OpenAI-compatible proxy that wraps all
CLI backends (Codex, Claude, Gemini, Kimi, OpenCode) behind a single HTTP
endpoint. When running CLIProxyAPI, you can point Aleph at the proxy and use the
API backend for everything:

```bash
# Start the proxy (see vendor/cliproxyapi for details)
# Then configure Aleph to use it:
export ALEPH_SUB_QUERY_BACKEND=api
export ALEPH_SUB_QUERY_URL=http://localhost:5000/v1
export ALEPH_SUB_QUERY_API_KEY=proxy
export ALEPH_SUB_QUERY_MODEL=your-model-name
```

This eliminates the need to configure individual CLI backends -- the proxy
handles routing and credential management for all of them.

### All Sub-Query Variables

| Variable                          | Description                                                    |
|-----------------------------------|----------------------------------------------------------------|
| `ALEPH_SUB_QUERY_BACKEND`        | Force backend: `api`, `claude`, `codex`, `gemini`, `kimi`, `opencode`, `auto` |
| `ALEPH_SUB_QUERY_SHARE_SESSION`  | Share the live Aleph MCP session with nested CLI sub-agents    |
| `ALEPH_SUB_QUERY_CODEX_MODE`     | Codex mode: `mcp` (default) or `exec`                          |
| `ALEPH_SUB_QUERY_CODEX_MODEL`    | Codex MCP model override (default: `gpt-5.4`)                  |
| `ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT` | Codex MCP reasoning effort (default: `low`)          |
| `ALEPH_SUB_QUERY_API_KEY`        | API key (fallback: `OPENAI_API_KEY`)                           |
| `ALEPH_SUB_QUERY_URL`            | API base URL (fallback: `OPENAI_BASE_URL`)                     |
| `ALEPH_SUB_QUERY_MODEL`          | Model name (required for API backend)                          |
| `ALEPH_SUB_QUERY_OPENCODE_MODEL` | OpenCode model override (default: `glm-5-turbo`)              |

Use `ALEPH_SUB_QUERY_BACKEND` to pin a backend or bypass auto-detection;
otherwise leave it unset for the default `auto` selection order.

Runtime `configure(sub_query_backend=...)` overrides auto-detection for the
active server session. The install/configure wizard now treats Codex as the
default sub-query path and pins the Codex MCP defaults when the CLI is
available.

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

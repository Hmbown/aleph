# MCP Server Configuration Guide

This guide explains how to properly configure aleph as an MCP server in **all major MCP-compatible clients**: Cursor, VS Code, Claude Desktop, OpenAI Codex, Windsurf, and others.
It focuses on `aleph`, which supports action tools and workspace scoping.

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

## Shared Sub-Query Sessions (Live Sandbox)

If you want CLI sub-agents spawned via `sub_query` to access the **same live Aleph session**
(tools, contexts, and sandbox state), enable streamable HTTP sharing:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": ["--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise"],
      "env": {
        "ALEPH_SUB_QUERY_SHARE_SESSION": "true",
        "ALEPH_SUB_QUERY_HTTP_PORT": "8765",
        "ALEPH_SUB_QUERY_MCP_SERVER_NAME": "aleph_shared"
      }
    }
  }
}
```

Notes:
- The Aleph server will spin up a **local streamable HTTP endpoint** on demand.
- CLI sub-agents (claude/codex/gemini) will be pointed at that live server automatically.
- Customize host/path with `ALEPH_SUB_QUERY_HTTP_HOST` and `ALEPH_SUB_QUERY_HTTP_PATH` if needed.
- Tools will be exposed under the server name you choose (default: `aleph_shared`).
- `aleph_shared` avoids conflicts with an existing `aleph` stdio entry in Codex config.
- For Claude, the shared session is passed via `--mcp-config` and `--strict-mcp-config` flags.

For even higher limits:
```json
{
  "args": ["--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise", "--timeout", "120", "--max-output", "100000"]
}
```

If you need stricter workspace scoping (single project only), continue below.

## The Workspace Root Issue

**Problem:** The aleph MCP server defaults to using the current working directory as the workspace root. If you launch Cursor/VS Code from your home directory, aleph will block file operations with:

```
Error: Path '/path/to/your-project/aleph/mcp/local_server.py' escapes workspace root '/path/to/your-home'
```

**Solution:** Explicitly set the workspace root in your MCP configuration.

## Cursor Configuration

Create or edit your Cursor MCP config:
- **macOS/Linux:** `~/.cursor/mcp.json`
- **Windows:** `%USERPROFILE%\.cursor\mcp.json`

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root",
        "/path/to/your-project",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

## VS Code Configuration

Create or edit your VS Code MCP config:
- **macOS/Linux:** `~/.vscode/mcp.json`
- **Windows:** `%USERPROFILE%\.vscode\mcp.json`

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root",
        "/path/to/your-project",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

## Claude Desktop Configuration

Claude Desktop auto-discovers MCP servers. To configure:

**Option 1: Auto-discovery (Simplest)**
```bash
aleph-rlm install claude-code
```

Then restart Claude Desktop - it will auto-discover aleph.

**Option 2: Manual Configuration**
Add to your Claude settings file:
- **macOS/Linux:** `~/.claude/settings.json`
- **Windows:** `%USERPROFILE%\.claude\settings.json`

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root",
        "/path/to/your-project",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

### Installing Claude Desktop Skill

For RLM workflow prompts, install the `/aleph` skill:

**Option 1:** Download [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and save to:
- macOS/Linux: `~/.claude/commands/aleph.md`
- Windows: `%USERPROFILE%\.claude\commands\aleph.md`

**Option 2:** From installed package:

<details>
<summary>macOS/Linux</summary>

```bash
mkdir -p ~/.claude/commands
cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" ~/.claude/commands/aleph.md
```
</details>

<details>
<summary>Windows (PowerShell)</summary>

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\commands"
$alephPath = python -c "import aleph; print(aleph.__path__[0])"
Copy-Item "$alephPath\..\docs\prompts\aleph.md" "$env:USERPROFILE\.claude\commands\aleph.md"
```
</details>

This enables the `/aleph` command for structured reasoning workflow.

## Codex CLI Configuration

Add to your Codex config:
- **macOS/Linux:** `~/.codex/config.toml`
- **Windows:** `%USERPROFILE%\.codex\config.toml`

```toml
[mcp_servers.aleph]
command = "aleph"
args = ["--enable-actions", "--tool-docs", "concise"]
```

To enable actions (read_file, write_file, etc.), use:

```toml
[mcp_servers.aleph]
command = "aleph"
args = ["--workspace-root", "/path/to/your-project", "--enable-actions", "--tool-docs", "concise"]
```

### Installing Codex Skill

For RLM workflow prompts, install the `$aleph` skill:

**Option 1:** Download [`docs/prompts/aleph.md`](docs/prompts/aleph.md) and save to:
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

This enables the `$aleph` command in Codex.

## Parameters Explained

These parameters apply to `aleph`:

- `--workspace-root <path>` - The root directory for file operations (read_file, write_file, run_command, etc.)
- `--workspace-mode <fixed|git|any>` - Path scope for actions: `fixed` (workspace root only), `git` (any git repo), `any` (no path restriction)
- `--enable-actions` - Enable action tools (read_file, write_file, run_command, run_tests, etc.)
- `--require-confirmation` - Require `confirm=true` on all action tool calls
- `--timeout <seconds>` - Sandbox execution timeout (default: 60)
- `--max-output <chars>` - Maximum output characters from commands (default: 50000)
- `--tool-docs <concise|full>` - Tool description verbosity for MCP clients (default: concise). Set `ALEPH_TOOL_DOCS=full` for full docs.
- `--max-file-size <bytes>` - Maximum file size for read operations (default: 1000000000)
- `--max-write-bytes <bytes>` - Maximum file size for write operations (default: 100000000)

## Sub-query backends

`sub_query` can use an API backend or a local CLI backend. When `ALEPH_SUB_QUERY_BACKEND` is `auto` (default), Aleph chooses the first available backend:

1. **API** - if API credentials are available
2. **claude CLI** - if installed
3. **codex CLI** - if installed
4. **gemini CLI** - if installed

### API Configuration

The API backend uses **OpenAI-compatible endpoints only**. Configure with these environment variables:

| Variable | Fallback | Description |
|----------|----------|-------------|
| `ALEPH_SUB_QUERY_API_KEY` | `OPENAI_API_KEY` | API key |
| `ALEPH_SUB_QUERY_URL` | `OPENAI_BASE_URL` | Base URL (default: `https://api.openai.com/v1`) |
| `ALEPH_SUB_QUERY_MODEL` | - | Model name (**required**) |

### Quick Setup Examples

**OpenAI:**
```bash
export ALEPH_SUB_QUERY_API_KEY=sk-...
export ALEPH_SUB_QUERY_MODEL=gpt-5.2-codex
```

**Groq (fast inference):**
```bash
export ALEPH_SUB_QUERY_API_KEY=gsk_...
export ALEPH_SUB_QUERY_URL=https://api.groq.com/openai/v1
export ALEPH_SUB_QUERY_MODEL=llama-3.3-70b-versatile
```

**Local LLM (Ollama, LM Studio, etc.):**
```bash
export ALEPH_SUB_QUERY_API_KEY=ollama  # Any non-empty value works
export ALEPH_SUB_QUERY_URL=http://localhost:11434/v1
export ALEPH_SUB_QUERY_MODEL=llama3.2
```

### Configuration Options

| Environment Variable | Description |
|---------------------|-------------|
| `ALEPH_SUB_QUERY_BACKEND` | Force backend: `api`, `claude`, `codex`, `gemini` |
| `ALEPH_SUB_QUERY_API_KEY` | API key (fallback: `OPENAI_API_KEY`) |
| `ALEPH_SUB_QUERY_URL` | API base URL (fallback: `OPENAI_BASE_URL`) |
| `ALEPH_SUB_QUERY_MODEL` | Model name (required for API backend) |

> **Note:** Some MCP clients don't reliably pass `env` vars from their config to the server process. If `sub_query` reports "API key not found" despite your client's MCP settings, add the exports to your shell profile:
> - **macOS/Linux:** `~/.zshrc` or `~/.bashrc`
> - **Windows:** System Environment Variables or `$PROFILE` in PowerShell
>
> Then restart your terminal/client.

For a full list of options, see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Finding Your Workspace Root

The workspace root should be the directory containing:
- Your `.git` folder (for git repositories)
- Your `pyproject.toml`, `package.json`, etc.
- The root of your project

**Automatic Detection:** If you don't set `--workspace-root`, aleph will:
1. Check `ALEPH_WORKSPACE_ROOT` env var (explicit override)
2. Check `PWD` env var (shell's invocation directory)
3. Check `INIT_CWD` env var (npm/yarn invocation directory)
4. Fall back to `os.getcwd()`
5. Walk up from the resolved directory to find `.git`

**Recommended:** Always set `--workspace-root` explicitly to avoid ambiguity.
If you need to work across multiple repos in one MCP server, prefer `--workspace-mode git` and use absolute paths (or a broad workspace root).

## Example Scenarios

### Scenario 1: Python Project

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root",
        "/Users/yourname/projects/my-python-app",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

### Scenario 2: Monorepo

For a monorepo, set workspace to a subdirectory:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root",
        "/Users/yourname/monorepo/packages/frontend",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

### Scenario 3: Remote Development

For development on remote machines:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root",
        "/remote/path/to/project",
        "--enable-actions",
        "--tool-docs",
        "concise",
        "--timeout",
        "60"
      ]
    }
  }
}
```

### Scenario 4: Increased Limits

Customize limits for your use case:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root", "/path/to/project",
        "--enable-actions",
        "--tool-docs",
        "concise",
        "--timeout", "60",
        "--max-output", "100000",
        "--max-file-size", "5000000000",
        "--max-write-bytes", "500000000"
      ]
    }
  }
}
```

Default limits:
- Timeout: 60 seconds
- Max command output: 50,000 characters
- Max file read: 1,000,000,000 bytes (1GB)
- Max file write: 100,000,000 bytes (100MB)

### Scenario 5: Any Git Repo

Allow action tools to operate in any git repo on the machine:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--enable-actions",
        "--tool-docs",
        "concise",
        "--workspace-mode",
        "git"
      ]
    }
  }
}
```
Use absolute paths in tool calls (or set `--workspace-root` to a broad parent) when working across repos.

## Security Considerations

### Actions Mode

When you enable `--enable-actions`, you grant aleph permission to:
- **Read files** - Read any file in workspace (up to 1GB by default)
- **Write files** - Create/modify files in workspace (up to 100MB by default)
- **Run commands** - Execute shell commands (30s timeout by default)
- **Run tests** - Execute test commands
Use `--workspace-mode git` to limit access to git repos, or `--workspace-mode any` to remove path restrictions.

### Confirmation Mode

Use `--require-confirmation` for safer operation:

```json
{
  "args": [
    "--workspace-root",
        "/path/to/project",
        "--enable-actions",
        "--tool-docs",
        "concise",
        "--require-confirmation"
  ]
}
```

When enabled, all action tools require `confirm=true` in the call.

### Adjusting Limits

Customize limits for your use case:

```json
{
  "args": [
    "--workspace-root", "/path/to/project",
    "--enable-actions",
    "--tool-docs",
    "concise",
    "--timeout", "60",
    "--max-output", "100000",
    "--max-file-size", "5000000000",
    "--max-write-bytes", "500000000"
  ]
}
```

Default limits:
- Timeout: 60 seconds
- Max command output: 50,000 characters
- Max file read: 1,000,000,000 bytes (1GB)
- Max file write: 100,000,000 bytes (100MB)

## Troubleshooting

### "Path escapes workspace root" Error

**Symptom:** File operations fail with path validation error.

**Cause:** Workspace root not set or incorrect.

**Solution:** Add `--workspace-root` to MCP configuration with the correct path.
If you intentionally want multi-repo access, use `--workspace-mode git` or `--workspace-mode any`.

**Examples:**

**Cursor:**
```json
{
  "mcpServers": {
    "aleph": {
      "args": [
        "--workspace-root",
        "/path/to/your-project",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

**VS Code:**
```json
{
  "mcpServers": {
    "aleph": {
      "args": [
        "--workspace-root",
        "/path/to/your-project",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

**Claude Desktop:**
```json
{
  "mcpServers": {
    "aleph": {
      "args": [
        "--workspace-root",
        "/path/to/your-project",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

**Codex:**
```toml
[mcp_servers.aleph]
command = "aleph"
args = ["--workspace-root", "/path/to/your-project", "--enable-actions", "--tool-docs", "concise"]
```

### "Actions are disabled" Error

**Symptom:** Action tools (read_file, write_file, etc.) return "Actions are disabled."

**Cause:** `--enable-actions` flag not set.

**Solution:** Add `--enable-actions` to MCP configuration.

**Examples for each client:**

All clients require adding `--enable-actions`:
- Cursor: Add to `args` array
- VS Code: Add to `args` array
- Claude Desktop: Add to `args` array
- Codex CLI: Add to `args` array

### MCP Server Not Starting

**Symptom:** Tools don't appear in Cursor/VS Code.

**Possible causes:**
1. aleph not installed: `pip install "aleph-rlm[mcp]"`
2. Entry point not available: Run `aleph --help` to test
3. Python not in PATH: Use full path to python/python3
4. Workspace root path incorrect
5. MCP client not restarted after config changes

**Debug steps:**
```bash
# Test if command works
aleph --help

# Check installation
pip show aleph-rlm

# Test server manually
python3 -m aleph.mcp.server --help

# Restart MCP client (Cursor/VS Code/Claude Desktop)
```

### sub_query Reports "API Key Not Found"

**Symptom:** `sub_query` tool returns "API key not found" errors despite having credentials configured.

**Cause:** Some MCP clients don't reliably pass `env` vars from their config to the server process.

**Solution:** Add credentials to your shell profile:

**macOS/Linux** (add to `~/.zshrc` or `~/.bashrc`):
```bash
export ALEPH_SUB_QUERY_API_KEY=sk-...
export ALEPH_SUB_QUERY_MODEL=gpt-5.2-codex
```

**Windows** (add to System Environment Variables, or PowerShell `$PROFILE`):
```powershell
$env:ALEPH_SUB_QUERY_API_KEY = "sk-..."
$env:ALEPH_SUB_QUERY_MODEL = "gpt-5.2-codex"
```

Then restart your terminal/MCP client.

**Note:** This is a client-side limitation, not an aleph bug.

## Other MCP Clients

### Windsurf

Windsurf uses standard MCP configuration files. Add to your MCP settings:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph",
      "args": [
        "--workspace-root",
        "/path/to/your-project",
        "--enable-actions",
        "--tool-docs",
        "concise"
      ]
    }
  }
}
```

### Cline / Continue.dev

These clients support standard MCP configuration. Check their documentation for exact file locations and format.

### Generic MCP Client

If your MCP client uses a different configuration system, the key parameters are:
- Command: `aleph`
- Required args: `--workspace-root /path/to/project`
- Optional args: `--enable-actions`, `--tool-docs <concise|full>`, `--require-confirmation`, `--timeout`, etc.

## Related Documentation

- [REMOTE_MCP_DIAGNOSIS.md](REMOTE_MCP_DIAGNOSIS.md) - Debugging remote MCP server issues
- [README.md](README.md) - Project overview and installation
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) - Full aleph configuration reference
- [docs/openai.md](docs/openai.md) - OpenAI-specific configuration

## Support

For issues specific to MCP configuration, please check:
1. Your MCP client documentation (Cursor, VS Code, Claude Desktop, Codex, Windsurf, etc.)
2. This configuration guide
3. Remote MCP diagnosis guide

For aleph-specific bugs or feature requests, please open an issue on GitHub.

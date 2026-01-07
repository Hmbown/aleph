# MCP Server Configuration Guide

This guide explains how to properly configure aleph as an MCP server in **all major MCP-compatible clients**: Cursor, VS Code, Claude Desktop, OpenAI Codex, Windsurf, and others.

## The Workspace Root Issue

**Problem:** The aleph MCP server defaults to using the current working directory as the workspace root. If you launch Cursor/VS Code from your home directory, aleph will block file operations with:

```
Error: Path '/Volumes/VIXinSSD/aleph/aleph/mcp/local_server.py' escapes workspace root '/Users/hunterbown'
```

**Solution:** Explicitly set the workspace root in your MCP configuration.

## Cursor Configuration

Create or edit `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root",
        "/Volumes/VIXinSSD/aleph",
        "--enable-actions"
      ]
    }
  }
}
```

## VS Code Configuration

Create or edit `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root",
        "/Volumes/VIXinSSD/aleph",
        "--enable-actions"
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
Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root",
        "/Volumes/VIXinSSD/aleph",
        "--enable-actions"
      ]
    }
  }
}
```

### Installing Claude Desktop Skill

For RLM workflow prompts in Claude Desktop, install the `/aleph` skill:

```bash
mkdir -p ~/.claude/commands
cp /path/to/aleph/docs/prompts/aleph.md ~/.claude/commands/aleph.md
```

This enables the `/aleph` command for structured reasoning workflow.

## Codex CLI Configuration

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.aleph]
command = "aleph-mcp-local"
args = ["--enable-actions"]
```

To enable actions (read_file, write_file, etc.), use:

```toml
[mcp_servers.aleph]
command = "aleph-mcp-local"
args = ["--workspace-root", "/Volumes/VIXinSSD/aleph", "--enable-actions"]
```

### Installing Codex Skill

For RLM workflow prompts in Codex CLI, install the skill:

```bash
mkdir -p ~/.codex/skills/aleph
cp /path/to/aleph/ALEPH.md ~/.codex/skills/aleph/SKILL.md
```

This enables aleph commands in Codex.

## Parameters Explained

All MCP servers support these parameters:

- `--workspace-root <path>` - The root directory for file operations (read_file, write_file, run_command, etc.)
- `--enable-actions` - Enable action tools (read_file, write_file, run_command, run_tests, etc.)
- `--require-confirmation` - Require `confirm=true` on all action tool calls
- `--timeout <seconds>` - Sandbox execution timeout (default: 30)
- `--max-output <chars>` - Maximum output characters from commands (default: 10000)
- `--max-read-bytes <bytes>` - Maximum file read size (default: 1000000)
- `--max-write-bytes <bytes>` - Maximum file write size (default: 1000000)

## Finding Your Workspace Root

The workspace root should be the directory containing:
- Your `.git` folder (for git repositories)
- Your `pyproject.toml`, `package.json`, etc.
- The root of your project

**Automatic Detection:** If you don't set `--workspace-root`, aleph will:
1. Check if `.git` exists in current directory
2. If not, search parent directories until finding `.git`
3. Use that directory as the workspace root

**Recommended:** Always set `--workspace-root` explicitly to avoid ambiguity.

## Example Scenarios

### Scenario 1: Python Project

```json
{
  "mcpServers": {
    "aleph": {
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root",
        "/Users/yourname/projects/my-python-app",
        "--enable-actions"
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
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root",
        "/Users/yourname/monorepo/packages/frontend",
        "--enable-actions"
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
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root",
        "/remote/path/to/project",
        "--enable-actions",
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
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root", "/path/to/project",
        "--enable-actions",
        "--timeout", "60",
        "--max-output", "50000",
        "--max-read-bytes", "5000000",
        "--max-write-bytes", "5000000"
      ]
    }
  }
}
```

Default limits:
- Timeout: 30 seconds
- Max command output: 10,000 characters
- Max file read: 1,000,000 bytes (1MB)
- Max file write: 1,000,000 bytes (1MB)

## Security Considerations

### Actions Mode

When you enable `--enable-actions`, you grant aleph permission to:
- **Read files** - Read any file in workspace (up to 1MB by default)
- **Write files** - Create/modify files in workspace (up to 1MB by default)
- **Run commands** - Execute shell commands (30s timeout by default)
- **Run tests** - Execute test commands

### Confirmation Mode

Use `--require-confirmation` for safer operation:

```json
{
  "args": [
    "--workspace-root",
        "/path/to/project",
        "--enable-actions",
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
    "--timeout", "60",
    "--max-output", "50000",
    "--max-read-bytes", "5000000",
    "--max-write-bytes", "5000000"
  ]
}
```

Default limits:
- Timeout: 30 seconds
- Max command output: 10,000 characters
- Max file read: 1,000,000 bytes (1MB)
- Max file write: 1,000,000 bytes (1MB)

## Troubleshooting

### "Path escapes workspace root" Error

**Symptom:** File operations fail with path validation error.

**Cause:** Workspace root not set or incorrect.

**Solution:** Add `--workspace-root` to MCP configuration with the correct path.

**Examples:**

**Cursor:**
```json
{
  "mcpServers": {
    "aleph": {
      "args": [
        "--workspace-root",
        "/Volumes/VIXinSSD/aleph",
        "--enable-actions"
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
        "/Volumes/VIXinSSD/aleph",
        "--enable-actions"
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
        "/Volumes/VIXinSSD/aleph",
        "--enable-actions"
      ]
    }
  }
}
```

**Codex:**
```toml
[mcp_servers.aleph]
command = "aleph-mcp-local"
args = ["--workspace-root", "/Volumes/VIXinSSD/aleph", "--enable-actions"]
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
1. aleph not installed: `pip install aleph[mcp]`
2. Entry point not available: Run `aleph-mcp-local --help` to test
3. Python not in PATH: Use full path to python/python3
4. Workspace root path incorrect
5. MCP client not restarted after config changes

**Debug steps:**
```bash
# Test if command works
aleph-mcp-local --help

# Check installation
pip show aleph

# Test server manually
python3 -m aleph.mcp.local_server --help

# Restart MCP client (Cursor/VS Code/Claude Desktop)
```

### sub_query Reports "API Key Not Found"

**Symptom:** `sub_query` tool returns "API key not found" errors despite having credentials configured.

**Cause:** Some MCP clients don't reliably pass `env` vars from their config to the server process.

**Solution:** Add credentials to your shell profile:
```bash
# For bash/zsh
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
export ANTHROPIC_API_KEY=...
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
      "command": "aleph-mcp-local",
      "args": [
        "--workspace-root",
        "/Volumes/VIXinSSD/aleph",
        "--enable-actions"
      ]
    }
  }
}
```

### Cline / Continue.dev

These clients support standard MCP configuration. Check their documentation for exact file locations and format.

### Generic MCP Client

If your MCP client uses a different configuration system, the key parameters are:
- Command: `aleph-mcp-local`
- Required args: `--workspace-root /path/to/project`
- Optional args: `--enable-actions`, `--require-confirmation`, `--timeout`, etc.

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

# Prompt: Install Aleph Across All Environments

Use this prompt with Claude Code (or any AI assistant with file access) to install Aleph on all your AI coding tools.

---

## Prompt

```
I need you to install and configure Aleph (an MCP server for recursive LLM reasoning over documents) across all my AI development environments.

**Target environments:**
1. Claude Code (CLI) - via ~/.claude/claude_desktop_config.json or similar
2. Claude Desktop - via ~/Library/Application Support/Claude/claude_desktop_config.json
3. Cursor - via cursor MCP config
4. Windsurf - via windsurf MCP config  
5. Codex CLI - via ~/.codex/config.toml

**Tasks:**

1. First, check if aleph-rlm is installed:
   ```bash
   pip show aleph-rlm || pip install aleph-rlm[mcp]
   ```

2. Run the automatic installer and see what it detects:
   ```bash
   aleph-rlm install
   ```

3. For any environments not auto-detected, manually configure:

   **Claude Desktop** (~/Library/Application Support/Claude/claude_desktop_config.json):
   ```json
   {
     "mcpServers": {
       "aleph": {
         "command": "aleph-mcp-local",
         "args": ["--enable-actions"]
       }
     }
   }
   ```

   **Codex CLI** (~/.codex/config.toml):
   ```toml
   [mcp_servers.aleph]
   command = "aleph-mcp-local"
   args = ["--enable-actions"]
   ```

4. Install the `/aleph` skill for Codex CLI:

   ```bash
   mkdir -p ~/.codex/skills/aleph
   cp /path/to/aleph/ALEPH.md ~/.codex/skills/aleph/SKILL.md
   ```

5. Set up the Mimo API key for sub_query functionality (needed for non-CLI environments like Claude Desktop):

   Create or update ~/.zshrc (or ~/.bashrc):
   ```bash
   # Aleph sub-query API (Xiaomi MiMo - free until Jan 20, 2026)
   export MIMO_API_KEY="<USER_NEEDS_TO_FILL_THIS>"
   export OPENAI_BASE_URL="https://api.xiaomimimo.com/v1"
   ```

   Get a free API key at: https://xiaomimimo.com

6. Verify installation:
   ```bash
   aleph-rlm doctor
   ```

7. Test by restarting each environment and checking if the aleph tools are available.

**Important notes:**
- The `--enable-actions` flag allows file read/write and command execution
- In CLI environments (Claude Code, Codex), sub_query will auto-detect and use the CLI itself - no API key needed
- In GUI environments (Claude Desktop), sub_query needs the Mimo API key
- The installer should handle most of this automatically, but verify each environment works

Please proceed step by step and report what you find in each environment.
```

---

## What this does

- Installs `aleph-rlm` Python package
- Configures MCP server in all supported AI coding environments
- Sets up Mimo API credentials for `sub_query` functionality
- Enables action tools (file I/O, commands) where appropriate

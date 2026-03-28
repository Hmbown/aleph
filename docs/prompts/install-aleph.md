# Prompt: Install Aleph Across All Environments

Use this prompt with Claude Code (or any AI assistant with file access) to install Aleph on all your AI coding tools.

---

## Prompt

```
I need you to install and configure Aleph (an MCP server for recursive LLM reasoning over large local data) across all my AI development environments.

**Target environments:**
1. Claude Code (CLI) - via ~/.claude/settings.json (macOS/Linux) or %USERPROFILE%\.claude\settings.json (Windows)
2. Claude Desktop - via ~/Library/Application Support/Claude/claude_desktop_config.json (macOS) or %APPDATA%\Claude\claude_desktop_config.json (Windows)
3. Cursor - via cursor MCP config
4. Windsurf - via windsurf MCP config
5. Codex CLI - via ~/.codex/config.toml (macOS/Linux) or %USERPROFILE%\.codex\config.toml (Windows)
6. Gemini CLI - via ~/.gemini/mcp.json
7. Kimi CLI - via ~/.kimi/mcp.json

**Tasks:**

1. First, check if aleph-rlm is installed:
   ```bash
   pip show aleph-rlm || pip install "aleph-rlm[mcp]"
   ```

2. Run the automatic installer and see what it detects:
   ```bash
   aleph-rlm install
   ```

   Choose an explicit install profile up front if needed:

   ```bash
   aleph-rlm install --profile claude
   aleph-rlm install --profile codex
   aleph-rlm install --profile portable
   ```

   Use `claude` when you want nested Claude sub-queries pinned with
   `--model opus` and `--effort low`. Use `codex` for the strongest validated
   shared-session path. Use `portable` if you do not want to pin a nested
   backend yet.

3. For any environments not auto-detected, manually configure:

   **Claude Desktop:**
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

   ```json
   {
      "mcpServers": {
        "aleph": {
          "command": "aleph",
          "args": ["--enable-actions", "--tool-docs", "concise", "--workspace-mode", "any"]
        }
      }
    }
   ```

   **Codex CLI:**
   - macOS/Linux: `~/.codex/config.toml`
   - Windows: `%USERPROFILE%\.codex\config.toml`

   ```toml
   [mcp_servers.aleph]
   command = "aleph"
   args = ["--enable-actions", "--tool-docs", "concise", "--workspace-mode", "any"]
   ```

   **Gemini CLI** (~/.gemini/mcp.json):
   ```json
   {
     "mcpServers": {
       "aleph": {
         "command": "aleph",
         "args": ["--enable-actions", "--tool-docs", "concise", "--workspace-mode", "any"]
       }
     }
   }
   ```

   **Kimi CLI** (~/.kimi/mcp.json):
   ```json
   {
     "mcpServers": {
       "aleph": {
         "transport": "stdio",
         "command": "aleph",
         "args": ["--enable-actions", "--tool-docs", "concise", "--workspace-mode", "any"]
       }
     }
   }
   ```

4. Install skills (optional but recommended):

   Download `docs/prompts/aleph.md` from the repo and save it to:

   **Claude Code:**
   - macOS/Linux: `~/.claude/commands/aleph.md`
   - Windows: `%USERPROFILE%\.claude\commands\aleph.md`

   **Codex CLI:**
   - macOS/Linux: `~/.codex/skills/aleph/SKILL.md`
   - Windows: `%USERPROFILE%\.codex\skills\aleph\SKILL.md`

   **Kimi CLI (recommended):**
   - User-level: `~/.config/agents/skills/aleph/SKILL.md`
   - Project-level: `.agents/skills/aleph/SKILL.md`

   Or from the installed package:
   ```bash
   # macOS/Linux
   mkdir -p ~/.codex/skills/aleph
   cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" ~/.codex/skills/aleph/SKILL.md

   # Kimi CLI (user-level)
   mkdir -p ~/.config/agents/skills/aleph
   cp "$(python -c "import aleph; print(aleph.__path__[0])")/../docs/prompts/aleph.md" ~/.config/agents/skills/aleph/SKILL.md
   ```

5. Configure `sub_query` (optional):

   If you want the API backend, set environment variables:

   **macOS/Linux** (add to ~/.zshrc or ~/.bashrc):
   ```bash
   export ALEPH_SUB_QUERY_API_KEY=sk-...
   export ALEPH_SUB_QUERY_MODEL=your-model-name
   # Optional: OpenAI-compatible base URL (Groq, Together, local LLMs, etc.)
   export ALEPH_SUB_QUERY_URL=https://api.your-provider.com/v1
   ```

   **Windows** (set via System Environment Variables or PowerShell $PROFILE):
   ```powershell
   $env:ALEPH_SUB_QUERY_API_KEY = "sk-..."
   $env:ALEPH_SUB_QUERY_MODEL = "your-model-name"
   ```

   CLI backends (`claude`, `codex`, `gemini`) do not require an API key. Aleph
   auto mode still resolves `codex -> api`, but install-time configs now choose
   an explicit profile instead of silently pinning Codex.

   If you want an all-Claude setup instead of the recommended Codex-backed
   nested path, explicitly add:

   **macOS/Linux**
   ```bash
   export ALEPH_SUB_QUERY_BACKEND=claude
   export ALEPH_SUB_QUERY_CLAUDE_MODEL=opus
   export ALEPH_SUB_QUERY_CLAUDE_EFFORT=low
   export ALEPH_SUB_QUERY_SHARE_SESSION=true
   ```

   **Windows**
   ```powershell
   $env:ALEPH_SUB_QUERY_BACKEND = "claude"
    $env:ALEPH_SUB_QUERY_CLAUDE_MODEL = "opus"
    $env:ALEPH_SUB_QUERY_CLAUDE_EFFORT = "low"
    $env:ALEPH_SUB_QUERY_SHARE_SESSION = "true"
   ```

6. Verify installation:
   ```bash
   aleph-rlm doctor
   ```

7. Test by restarting each environment and checking if the aleph tools are available.

**Important notes:**
- The `--enable-actions` flag allows file read/write and command execution
- `--enable-actions` also enables rg-based search, smart loaders, and memory pack auto-save
- In CLI environments (Claude Code, Codex, Gemini), `sub_query` can use the local CLI backend - no API key needed
- Preferred install flow: choose `portable`, `claude`, `codex`, or `api` during `aleph-rlm install`
- Claude profile pins nested Claude sub-queries with `opus` and `low` effort
- The installer should handle most of this automatically, but verify each environment works
- For per-project scoping, set `--workspace-root /absolute/path/to/project` instead of `--workspace-mode git`. See MCP_SETUP.md for details.
- Some MCP clients don't reliably pass `env` vars from config to the server process. If `sub_query` reports missing credentials, add exports to your shell profile and restart the client.

Please proceed step by step and report what you find in each environment.
```

---

## What this does

- Installs `aleph-rlm` Python package
- Configures MCP server in all supported AI coding environments
- Optionally sets up OpenAI-compatible API credentials for `sub_query`
- Enables action tools (file I/O, commands) where appropriate

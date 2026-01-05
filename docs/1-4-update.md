# Aleph RLM Enhancement Plan
> January 4, 2026

## Goal

Enable **Recursive Language Model (RLM)** patterns in Aleph - allowing the host AI to spawn sub-agents that can reason over context slices, without requiring external API keys.

Based on: [Recursive Language Models (arXiv:2512.24601)](https://arxiv.org/abs/2512.24601)

---

## Core Insight

The RLM paper's key breakthrough: **treat context as an external environment variable**, then let the LLM programmatically decompose and recursively query itself over chunks.

Aleph already has:
- ✅ Context as `ctx` variable
- ✅ REPL with 80+ helpers (`peek`, `search`, `chunk`, etc.)
- ✅ Evidence/citation tracking
- ✅ Variable persistence

**What's missing**: True recursive sub-LLM calls where the sub-agent can also use tools.

---

## Architecture: CLI-Based Sub-Agent Spawning

### The Problem
MCP servers can't call themselves - tools are invoked BY the host AI, not by Aleph.

### The Solution
Spawn sub-agents via CLI tools that the user is already authenticated with:

```
┌─────────────────────────────────────────────────────────┐
│ Host AI (Claude Desktop / Cursor / Codex)               │
│   └─ calls Aleph MCP tools                              │
│       └─ sub_query(prompt, context_slice)               │
│           └─ spawns: claude -p "prompt" --no-input      │
│               └─ returns result to Aleph                │
│                   └─ Aleph returns to Host AI           │
└─────────────────────────────────────────────────────────┘
```

### Supported CLI Backends

| Backend | Command | Notes |
|---------|---------|-------|
| `claude` | `claude --dangerously-skip-permissions -p "prompt"` | Claude Code CLI |
| `codex` | `codex -q "prompt"` | OpenAI Codex CLI |
| `aider` | `aider --message "prompt" --yes` | Aider CLI |
| `api` | Direct API call | Requires API key (fallback) |

---

## Implementation Steps

### Step 1: Add SubQueryBackend Configuration

Create `aleph/sub_query/__init__.py`:

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class SubQueryConfig:
    """Configuration for sub-query backend."""
    backend: Literal["claude", "codex", "aider", "api", "auto"] = "auto"
    
    # CLI options
    cli_timeout_seconds: float = 120.0
    cli_max_output_chars: int = 50_000
    
    # API fallback options (if backend="api")
    # Default: Mimo Flash V2 (free public beta until Jan 20, 2026)
    # Uses OpenAI-compatible API format
    api_base_url_env: str = "OPENAI_BASE_URL"
    api_key_env: str = "OPENAI_API_KEY"
    api_model: str = "mimo-v2-flash"
    
    # Behavior
    max_context_chars: int = 100_000  # truncate context slice if too long
    include_system_prompt: bool = True
```

### Step 2: Implement CLI Backend Runner

Create `aleph/sub_query/cli_backend.py`:

```python
import asyncio
import shlex
from pathlib import Path

async def run_cli_sub_query(
    prompt: str,
    context_slice: str | None,
    backend: str,
    timeout: float = 120.0,
    cwd: Path | None = None,
) -> tuple[bool, str]:
    """
    Spawn a CLI sub-agent and return its response.
    
    Returns (success, output)
    """
    
    # Build the full prompt
    full_prompt = prompt
    if context_slice:
        full_prompt = f"{prompt}\n\n---\nContext:\n{context_slice}"
    
    # Escape for shell
    escaped_prompt = shlex.quote(full_prompt)
    
    # Build command based on backend
    if backend == "claude":
        cmd = f'claude --dangerously-skip-permissions -p {escaped_prompt} --no-input'
    elif backend == "codex":
        cmd = f'codex -q {escaped_prompt}'
    elif backend == "aider":
        cmd = f'aider --message {escaped_prompt} --yes --no-git'
    else:
        return False, f"Unknown CLI backend: {backend}"
    
    # Run the command
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout
        )
        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            return False, f"CLI error (exit {proc.returncode}): {err}"
        return True, output
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"CLI timeout after {timeout}s"
```

### Step 3: Implement API Fallback Backend

Create `aleph/sub_query/api_backend.py`:

```python
import os
import httpx

async def run_api_sub_query(
    prompt: str,
    context_slice: str | None,
    model: str = "mimo-v2-flash",
    api_key_env: str = "OPENAI_API_KEY",
    api_base_url_env: str = "OPENAI_BASE_URL",
    timeout: float = 60.0,
) -> tuple[bool, str]:
    """
    Run sub-query via OpenAI-compatible API.
    
    Default: Mimo Flash V2 (free public beta until Jan 20, 2026)
    
    Returns (success, output)
    """
    api_key = os.environ.get(api_key_env)
    base_url = os.environ.get(api_base_url_env, "https://api.openai.com/v1")
    
    if not api_key:
        return False, f"API key not found in ${api_key_env}. Set OPENAI_API_KEY for Mimo Flash V2."
    
    full_prompt = prompt
    if context_slice:
        full_prompt = f"{prompt}\n\n---\nContext:\n{context_slice}"
    
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": full_prompt}],
        "max_tokens": 8192,
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            
            if resp.status_code != 200:
                return False, f"API error {resp.status_code}: {resp.text}"
            
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return True, text
        except httpx.TimeoutException:
            return False, f"API timeout after {timeout}s"
        except (KeyError, IndexError) as e:
            return False, f"Failed to parse API response: {e}"
        except Exception as e:
            return False, f"API request failed: {e}"
```

### Step 4: Add `sub_query` MCP Tool

Update `aleph/mcp/local_server.py` - add this tool registration:

```python
@self.server.tool()
async def sub_query(
    prompt: str,
    context_slice: str | None = None,
    context_id: str = "default",
    backend: str = "auto",
) -> str:
    """
    Run a sub-query using a spawned sub-agent.
    
    This enables RLM-style recursive reasoning: break your problem
    into chunks, query a sub-agent for each chunk, aggregate results.
    
    Args:
        prompt: The question/task for the sub-agent
        context_slice: Optional context to include (from ctx variable)
        context_id: Session to record evidence in
        backend: "auto", "claude", "codex", "aider", or "api"
    
    Returns:
        The sub-agent's response
    
    Example usage in exec_python:
        chunks = chunk(100000)
        results = []
        for i, c in enumerate(chunks):
            answer = sub_query(f"Summarize chunk {i}", context_slice=c)
            results.append(answer)
        final = sub_query("Combine these summaries: " + str(results))
    """
    session = self._sessions.get(context_id)
    if session:
        session.iterations += 1
    
    # Auto-detect backend
    resolved_backend = backend
    if backend == "auto":
        resolved_backend = _detect_cli_backend()
    
    # Truncate context if needed
    if context_slice and len(context_slice) > self.sub_query_config.max_context_chars:
        context_slice = context_slice[:self.sub_query_config.max_context_chars] + "\n...[truncated]"
    
    # Try CLI first, fall back to API
    if resolved_backend in ("claude", "codex", "aider"):
        success, output = await run_cli_sub_query(
            prompt=prompt,
            context_slice=context_slice,
            backend=resolved_backend,
            timeout=self.sub_query_config.cli_timeout_seconds,
            cwd=self.action_config.workspace_root if self.action_config else None,
        )
    else:
        success, output = await run_api_sub_query(
            prompt=prompt,
            context_slice=context_slice,
            model=self.sub_query_config.api_model,
            api_key_env=self.sub_query_config.api_key_env,
            api_base_url_env=self.sub_query_config.api_base_url_env,
        )
    
    # Record evidence
    if session and success:
        session.evidence.append(_Evidence(
            source="sub_query",
            line_range=None,
            pattern=None,
            snippet=output[:200],
            note=f"backend={resolved_backend}",
        ))
    
    if not success:
        return f"## Sub-Query Error\n\n{output}"
    
    return f"## Sub-Query Result\n\n{output}"


def _detect_cli_backend() -> str:
    """Auto-detect available CLI backend."""
    import shutil
    
    if shutil.which("claude"):
        return "claude"
    if shutil.which("codex"):
        return "codex"
    if shutil.which("aider"):
        return "aider"
    return "api"  # fallback
```

### Step 5: Inject `sub_query` into REPL Namespace

Update `REPLEnvironment` to expose `sub_query` as a callable inside `exec_python`:

```python
# In sandbox.py, after inject_sub_query:

def inject_sub_query(self, fn: SubQueryFn) -> None:
    """Inject sub_query(prompt, context_slice=None) into the REPL namespace."""
    self._sub_query_fn = fn
    self._namespace["sub_query"] = self._sync_bridge(fn)
```

This already exists! We just need to wire it up in local_server.py.

### Step 6: Update Configuration & Environment

Add to `.env.example`:
```bash
# =============================================================================
# Sub-Query API Configuration (for RLM-style recursive reasoning)
# =============================================================================
# Required if no CLI backend (claude/codex/aider) is available.
#
# Default: Mimo Flash V2 - FREE public beta until Jan 20, 2026
# A strong reasoning model, perfect for sub-agent tasks.
#
# Get your free API key at: https://mimo.ai
# Then set these environment variables:

OPENAI_API_KEY=your_mimo_api_key_here
OPENAI_BASE_URL=https://api.mimo.ai/v1

# Model to use for sub-queries (default: mimo-v2-flash)
# ALEPH_SUB_QUERY_MODEL=mimo-v2-flash

# =============================================================================
# Alternative: Use any OpenAI-compatible API
# =============================================================================
# Examples:
#   OpenAI:     OPENAI_BASE_URL=https://api.openai.com/v1
#   Groq:       OPENAI_BASE_URL=https://api.groq.com/openai/v1
#   Together:   OPENAI_BASE_URL=https://api.together.xyz/v1
#   Local:      OPENAI_BASE_URL=http://localhost:11434/v1 (Ollama)
```

### Step 7: Add Documentation

Update README.md with:
```markdown
## Recursive Sub-Queries (RLM Mode)

Aleph supports RLM-style recursive reasoning. Inside `exec_python`, you can call:

```python
# Break context into chunks and process each
chunks = chunk(100000)  # 100k char chunks
summaries = []
for c in chunks:
    summary = sub_query("Summarize this section:", context_slice=c)
    summaries.append(summary)

# Aggregate
final = sub_query(f"Combine these {len(summaries)} summaries into a final answer")
print(final)
```

**Backend Priority:**
1. `claude` CLI (if installed) - uses your existing Claude subscription
2. `codex` CLI (if installed) - uses your existing OpenAI subscription
3. `aider` CLI (if installed)
4. **Mimo Flash V2 API** (FREE until Jan 20, 2026) - strong reasoning model

To use the API fallback, set in your environment:
```bash
export OPENAI_API_KEY=your_mimo_key
export OPENAI_BASE_URL=https://api.mimo.ai/v1
```

Set `ALEPH_SUB_QUERY_BACKEND=api` to force API mode.
```

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `aleph/sub_query/__init__.py` | Create | SubQueryConfig dataclass |
| `aleph/sub_query/cli_backend.py` | Create | CLI spawning logic |
| `aleph/sub_query/api_backend.py` | Create | API fallback (Gemini) |
| `aleph/mcp/local_server.py` | Modify | Add `sub_query` tool, wire up config |
| `aleph/repl/sandbox.py` | Verify | Already has `inject_sub_query` |
| `.env.example` | Create/Update | Document env vars |
| `README.md` | Update | Document RLM mode |
| `tests/test_sub_query.py` | Create | Unit tests |

---

## Testing Plan

1. **CLI backend test**: Mock subprocess, verify command construction
2. **API backend test**: Mock httpx, verify Gemini request format
3. **Integration test**: Load context, run `exec_python` with `sub_query` calls
4. **E2E test**: Actually spawn Claude CLI (manual, requires Claude Code installed)

---

## Rollout

1. **Phase 1** (this PR): CLI backends + Gemini API fallback
2. **Phase 2**: Parallel batch sub-queries (`batch_sub_query`)
3. **Phase 3**: Sub-agent can use Aleph tools (true recursion via MCP-over-stdio)

---

## Open Questions

- [ ] Should sub_query results auto-cite into evidence?
- [ ] Rate limiting for API fallback?
- [ ] Should we support streaming responses?
- [ ] Token counting for sub-queries?

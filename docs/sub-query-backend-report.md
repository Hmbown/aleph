# Sub-Query Backend Validation Report

**Date:** 2026-03-08
**Validated against:** Aleph from `main` (commit `163f019`), editable install
**Test environment:** macOS Darwin 25.1.0, Python 3.12, Claude Code (Opus 4.6)

---

## Architecture Clarification

Aleph's sub-query system has two modes of context delivery:

1. **Prompt-embedded** (`share_session=false`): The parent slices the context and pastes it directly into the sub-agent's prompt. Simple, but the sub-agent cannot explore beyond the slice. Suitable for small contexts and simple questions.

2. **Shared-session MCP** (`share_session=true`): Aleph starts a streamable HTTP server (`http://127.0.0.1:8765/mcp` by default) that exposes its own MCP tools (`search_context`, `peek_context`, `exec_python`, etc.). The sub-query backend CLI is launched with MCP configuration that points it back to this server. The sub-agent then uses Aleph's tools to interactively explore the parent's in-memory contexts.

Runtime default remains `share_session=false`, but generated Codex configs pin
it to `true`, which is the validated recommended setup.

**The key requirement for shared-session mode:** the sub-query backend CLI must be capable of accepting per-session MCP server configuration at launch time. The mechanism for this varies by CLI — and this is the primary differentiator between backends.

### How Each Backend Receives MCP Config

| Backend | Mechanism | Dynamic? | Bridge Required? |
|---------|-----------|----------|------------------|
| **Codex** | Native MCP transport via `-c mcp_servers.NAME.transport=streamable_http` | Yes | No |
| **Claude** | `--mcp-config FILE --strict-mcp-config` (temp JSON file) | Yes | No |
| **Gemini** | `GEMINI_CLI_SYSTEM_SETTINGS_PATH` env var pointing to temp JSON file | Yes | No |
| **Copilot** | `--additional-mcp-config @FILE` (temp JSON file, stdio only) | Yes | Yes (`mcp-remote`) |
| **Qwen** | `qwen mcp add NAME` (persistent user settings) | No | Yes (`mcp-remote`) |
| **API** | N/A — context is prompt-embedded, no MCP | N/A | N/A |

---

## Backend Rankings

### Tier 1: Production Default

#### 1. Codex (MCP mode) — Score: 9.5/10

The only backend with native streamable HTTP MCP transport. Aleph launches Codex with `codex mcp-server -c mcp_servers={}` (clean slate, no inherited config) and communicates via the MCP protocol directly — no bridge process, no temp files, no shell escaping.

**Strengths:**
- Native MCP transport — zero config leakage, zero bridge overhead
- Thread persistence via `thread_id` — enables multi-turn reasoning within a single sub-query
- Cleanest output formatting (bare values, no markdown wrapping or source-code quotes)
- Model/reasoning-effort tunable (`ALEPH_SUB_QUERY_CODEX_MODEL`, `ALEPH_SUB_QUERY_CODEX_REASONING_EFFORT`)
- Already the auto-detected default when `codex` CLI is installed

**Weaknesses:**
- 180s default timeout is too short for complex multi-search tasks at `reasoning_effort=low`
- Requires Codex CLI installed (not available in all environments)

**Test results:** 6/7 tests passed. One timeout on a complex multi-search task (resolved by bumping timeout to 300s or simplifying the query).

---

### Tier 2: Explicit Fallbacks (Stable)

#### 2. Claude — Score: 8/10

Reliable shared-session fact retrieval. Stateless non-interactive invocation (`--dangerously-skip-permissions --no-session-persistence --output-format json`) is clean and predictable.

**Strengths:**
- Inline per-session MCP config via `--mcp-config FILE`
- `--strict-mcp-config` prevents inheriting user's other MCP servers — no config leakage
- Accurate results on all tests
- Stable invocation — did not hang, did not produce malformed output

**Weaknesses:**
- Slightly over-formatted output (backtick wrapping around values: `` `gpt-5.4` `` instead of `gpt-5.4`)
- No thread persistence (each sub-query is stateless)
- Not auto-selected — requires explicit `ALEPH_SUB_QUERY_BACKEND=claude` or `configure(sub_query_backend="claude")`

**Test results:** 3/3 tests passed.

#### 3. Gemini — Score: 6.5/10

Functional but noisier. Results are accurate, but output formatting and namespace behavior are less predictable.

**Strengths:**
- Inline MCP config via env var (`GEMINI_CLI_SYSTEM_SETTINGS_PATH`)
- Headless JSON output mode (`-o json`)
- Accurate results on all tests

**Weaknesses:**
- Output includes Python source-code formatting (wraps values in quotes from the source: `"gpt-5.4"` instead of `gpt-5.4`)
- Namespace pollution observed: after a Gemini sub-query, the REPL's "Variables Updated" report showed internal functions (`get_config`, `set_backend`, `sub_aleph`, `sub_query`) as modified — likely a reporting artifact rather than an actual clobber, but warrants investigation
- Config leakage risk is moderate — the env-var approach replaces the settings file wholesale, but Gemini's extension system (`--extensions ""` is used to suppress) can still load ambient state
- Not auto-selected — requires explicit override

**Test results:** 3/3 tests passed (with caveats on output cleanliness).

---

### Tier 3: Candidates (Not Yet Integrated)

#### 4. GitHub Copilot CLI — Score: 7/10 (potential)

Successfully completed the MCP fact-retrieval test via `mcp-remote` bridge. Has the best inline MCP config story among non-integrated candidates.

**Strengths:**
- `--additional-mcp-config @FILE` accepts per-session MCP config (JSON file) — no persistent state pollution
- `--allow-all-tools` enables fully non-interactive operation
- Uses `gpt-5.4` by default — same model as Codex, comparable output quality
- `--output-format text` produces clean results (usage stats appended but easily stripped)
- MCP test passed: correctly called `search_context`, returned exact fact value

**Weaknesses:**
- Requires `mcp-remote` bridge (Copilot only supports stdio MCP servers, not streamable HTTP)
- MCP config format requires `mcpServers` wrapper and `command`/`args` — cannot specify HTTP URLs directly
- JSON output is extremely verbose (streaming deltas, encrypted content blobs, reasoning traces)
- Usage stats appended to text output need stripping
- Not yet integrated into Aleph's `cli_backend.py`

**Integration effort:** Moderate. Needs: (1) output parser for text mode (strip usage stats), (2) MCP config generator using mcp-remote bridge, (3) `copilot` entry in `BackendType` and `detect_backend()`.

#### 5. Qwen CLI — Score: 5/10 (potential)

MCP test passed, but the configuration model is unsuitable for dynamic shared-session use.

**Strengths:**
- MCP fact-retrieval test passed — correct result, clean text output
- `--yolo -o text` enables fully non-interactive operation
- Fast and lightweight

**Weaknesses:**
- **No inline per-session MCP config** — only supports persistent `qwen mcp add/remove` which modifies user settings. This means every shared-session sub-query would need to add then remove the Aleph server, risking race conditions and polluting user config.
- **Config leakage** — inherits ALL user-configured MCP servers. In testing, Qwen's JSON output showed Linear, Notion, and other unrelated MCP servers being loaded. There is no `--extensions ""` or `--strict-mcp-config` equivalent to suppress this.
- Requires `mcp-remote` bridge (same as Copilot)

**Integration effort:** High. Blocked until Qwen adds either: (a) `--mcp-config FILE` flag for per-session config, (b) an environment variable to override the settings directory, or (c) a way to suppress inherited MCP servers.

---

### Tier 4: Not Directly Comparable

#### API Backend — Score: 6/10 (hypothesized)

The API backend was not tested in this validation run, but its behavior can be characterized from code analysis.

**How it works:** The API backend (`aleph/sub_query/api_backend.py`) sends the query + a truncated context slice directly to an OpenAI-compatible API endpoint. There is no MCP server involvement — the context is prompt-embedded, not tool-accessible. The sub-agent receives a single static slice and must answer from that alone.

**Hypothesized strengths:**
- No CLI dependency — works anywhere with an API key (`OPENAI_API_KEY` or `ALEPH_SUB_QUERY_API_KEY`)
- No MCP server startup, no bridge, no config files
- Fastest path for simple questions where context fits in a single slice
- Works in CI/CD, containers, and other environments where CLI tools may not be installed

**Hypothesized weaknesses:**
- Cannot interactively explore the context — if the answer isn't in the initial slice, it fails
- Context is truncated to `max_context_chars` (default 20,000) — large contexts are lossy
- No tool use — cannot search, peek at specific ranges, or run code against the context
- Quality degrades as context size increases (needle-in-haystack problem)
- No thread persistence

**When to use:** When CLI backends are unavailable, or when the context is small and the question is simple. Falls back to this automatically if no CLI is detected (`auto` resolution: `codex -> api`).

---

## Shared-Session vs. Prompt-Embedded: Decision Framework

| Factor | Shared-Session MCP | Prompt-Embedded | API Backend |
|--------|-------------------|-----------------|-------------|
| **Context access** | Interactive (search, peek, exec) | Single static slice | Single static slice |
| **Max effective context** | Unlimited (in-memory) | ~20K chars (truncated) | ~20K chars (truncated) |
| **Requires CLI** | Yes | Yes | No |
| **Requires MCP support** | Yes | No | No |
| **Startup overhead** | HTTP server + MCP handshake | None | None |
| **Best for** | Large contexts, exploratory queries | Small contexts, direct questions | No-CLI environments |

---

## Observed Issues and Recommendations

### Issues Found

1. **Validation regex leaks to the sub-agent via retry prompt.** When `sub_query_strict` retries after a validation failure, the retry prompt says "respond again and match the required format exactly" — but the sub-agent sees the regex pattern from the error context. In testing, the sub-agent gamed an impossible regex by returning the literal pattern string. **Recommendation:** The retry prompt should describe the expected format semantically (e.g., "return a 4-digit year") rather than exposing the regex.

2. **Nonexistent `context_id` errors are silently swallowed.** When a sub-agent requests a context that doesn't exist, the MCP server returns an empty result instead of a clear error. The sub-agent then falls back to guessing or using a different context. **Recommendation:** Return an explicit error message listing available context IDs.

3. **`validate_regex` vs `validation_regex` naming inconsistency.** The `sub_query_strict()` REPL helper parameter is `validate_regex`, but the `SubQueryConfig` dataclass field is `validation_regex`. **Recommendation:** Align to one name.

4. **180s default timeout is marginal for complex shared-session tasks.** A multi-search task across a 9K-char file timed out at `reasoning_effort=low`. Simple fact retrieval completes in ~30-60s. **Recommendation:** Either increase the default to 300s, or document that `configure(sub_query_timeout=300)` should be called before depth-3+ recursion.

5. **`load_context(context=PATH)` loads the path string, not the file.** Using `context="/path/to/file"` loaded the 51-character path string as the context content. The correct call is `load_file(path="/path/to/file")`. **Recommendation:** Document this distinction clearly, or detect file paths in `context=` and suggest `load_file` instead.

### Documentation Recommendations

The README and configuration docs should clarify:

- **Shared-session architecture:** Explain that the sub-query CLI must connect back to Aleph's HTTP server, and that this is why each backend needs a specific MCP config injection mechanism.
- **Backend auto-resolution:** `auto` resolves to `codex` (if installed) then `api`. Claude and Gemini are explicit overrides only.
- **When to use each backend:** Quick reference for which backend to choose based on environment constraints.
- **Timeout guidance:** Default 180s is sufficient for simple queries; bump to 300s+ for complex multi-step exploration.
- **Copilot as a future candidate:** Note that it passed MCP integration testing and is architecturally compatible, pending formal integration.

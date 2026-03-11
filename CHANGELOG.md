# Changelog

## 0.8.7

- Docs: Simplified the README into a shorter front door focused on what Aleph
  is, how to start, and how to use it safely.
- Docs: Synced the web landing page copy with the README so the product page
  tells the same load/search/compute/recurse story.
- Chore: Updated `scripts/sync_versions.py` to keep the landing page version
  badge in sync with the package version.
- Release: Finalized the 0.8.7 docs-and-positioning refresh.

## 0.8.6

- Fixed: `detect_backend()` now respects an explicit `SubQueryConfig.backend`
  before consulting environment-based auto-detection.
- Fixed: MCP status/config snapshots now report the active programmatic
  sub-query backend instead of always echoing `ALEPH_SUB_QUERY_BACKEND`.
- Improved: `aleph-rlm install` / `configure` now preselect `codex` when the
  CLI is already installed, reducing friction for Codex users.
- Docs: Added a practical Aleph smoke-test flow, clarified backend selection
  precedence, documented workspace-root save/load behavior, and synced the web
  landing page copy/version with the package release.
- Tests: Added coverage for the installer backend default, programmatic backend
  precedence, and MCP status snapshot reporting.

## 0.8.5

- Added: Deployment profiles (`trusted` / `isolated`) via
  `ALEPH_CONTEXT_POLICY` env var and `configure(context_policy=...)`.
  Isolated mode requires `confirm=true` for session save/load and disables
  auto memory-pack.
- Added: RLM output feedback mode (`full` / `metadata`) via
  `ALEPH_OUTPUT_FEEDBACK` env var and `configure(output_feedback=...)`.
  Metadata mode reports dimensions (line counts, char counts, return types)
  without raw content, reducing context window consumption.
- Improved: Blocked-tool messages in isolated mode now include actionable
  alternatives (`exec_python`, `peek_context`, `search_context`) and a
  hint to switch policy via `configure()`.
- Improved: `configure()` returns detailed guidance when switching context
  policy, explaining what changed.
- Improved: Recipe `map_sub_query` now runs sub-queries in parallel
  (`asyncio.gather` with semaphore, max 10 concurrent) and checks budget
  upfront before dispatching.
- Refactored: Session serialization consolidated into `aleph/mcp/session.py`
  as canonical source. Removed ~200 lines of duplicated code from
  `local_server.py`.
- Added: Typed `EvidenceSource` Literal and `sub_aleph` evidence source.
- Tests: 20+ new tests covering policy UX messaging, output feedback modes,
  context isolation regressions, and serialization consolidation.
- Docs: Added deployment profiles and output feedback mode sections to
  CONFIGURATION.md.

## 0.8.4

- Security: Raw context preview omitted from default system prompt
  (`[OMITTED FOR CONTEXT ISOLATION]`) to prevent unintentional context leakage.
- Security: `get_variable("ctx")` blocked at the MCP boundary with a clear
  error directing users to `exec_python`.
- Fixed: `exec_python` return values are now truncated at the sandbox layer,
  preventing large context strings from leaking through `repr()` of the
  return value.
- Fixed: MCP execution result formatting applies `_truncate_tool_text` to
  stdout, stderr, return value, and the final assembled output independently.
- Added: `ALEPH_MAX_TOOL_RESPONSE_CHARS` env var (default 10,000) for MCP
  tool response cap.
- Added: Defense-in-depth truncation in `tool_registry.py` `get_variable`
  and `exec_python` output paths.
- Tests: New regression tests in `test_context_isolation_regressions.py`,
  `test_sandbox.py` (return value truncation), and
  `test_mcp_local_server_regressions.py` (MCP-level truncation).
- Docs: Added "Context Isolation and Safety" section to README, updated
  CONFIGURATION.md and debug diagnostic doc.

## 0.8.1

- Docs: Rewrote README `$aleph` sections to document the intended Codex flow
  (file path -> load into Aleph memory -> immediate analysis).
- Docs: Clarified `/aleph` vs `$aleph` invocation and added explicit verification
  checks for skill + MCP wiring.
- Release: Bumped package version metadata to 0.8.1.

## 0.7.11

- Fixed: MCP `rg_search` now accepts `paths` as string or list; previously a
  string caused a Pydantic validation error.

## 0.7.10

- Fixed: Removed unsupported `color` kwarg from `ArgumentParser` that crashed
  on Python < 3.14 (and in Docker containers using older Python).

## 0.7.9

- Improved: Increased sub-query timeout defaults
  (CLI: 120 s -> 300 s, API: 60 s -> 120 s).

## 0.7.8

- Docs: Clarified CLI aliases (`aleph run` / `aleph-rlm run`) and updated MCP
  manual config args.

## 0.7.7

- Added: `aleph-rlm configure` wizard for MCP client configs (workspace scope,
  sub-query backend, Docker).
- Added: `aleph run|shell|serve` and `aleph-rlm run|shell|serve` as official
  CLI entry points; `alef` is now deprecated.
- Added: Optional Dockerfile and install flow for containerized MCP server.
- Fixed: Headless argparse colorization crash when stdout/stderr are closed.
- Improved: Cached text context analysis to avoid repeat scans.

## 0.7.6

- Removed: CoCap module and related REPL helpers.

## 0.7.5

- Added: CLI provider tests for message formatting and error handling.
- Added: Swarm coordination/progress/context-id test coverage.
- Added: `ctx_append`/`ctx_set` sandbox helper tests.
- Fixed: Swarm timestamps now use timezone-safe UTC generation.
- Fixed: `__version__` now matches the package release version.

## 0.7.4

- Added: `alef` CLI command for running full RLM loop without MCP server.
- Added: CLI provider (`--provider cli`) for API-key-free operation via
  `claude`, `codex`, or `gemini` CLIs.
- Added: Entry point `alef run "prompt" --provider cli --model claude` with
  context file/stdin support.
- Fixed: Code blocks before FINAL directives are now executed properly
  (parser priority fix).
- Fixed: Trajectory JSON serialization for `ActionType` enum.
- Fixed: Type annotations for mypy compliance.

## 0.7.3

- Added: CLI recursion tracking and depth controls for `sub_aleph`.
- Improved: Session management for nested recursion contexts.

## 0.7.2

- Added: `sub_aleph` nested recursion tool for RLM-style recursive reasoning
  with depth control.
- Added: MCP and REPL exposure for `sub_aleph` with configurable `max_depth`,
  `max_iterations`, and `max_sub_queries`.
- Added: `ALEPH_MAX_DEPTH` environment variable for limiting recursion depth.
- Added: Double recursion test (`tests/test_double_recursion.py`) for
  deterministic verification.
- Updated: Docs for `sub_aleph` usage patterns and depth configuration.

## 0.7.1

- Enhanced: System prompt with RLM paper examples
  (arXiv:2512.24601 Appendix D patterns).
- Added: Sub-query batching efficiency guidance (~100-200 K chars per call,
  avoid 1000s of small calls).
- Added: New /aleph skill examples -- iterative document analysis,
  regex-targeted sub-queries, answer verification pattern.
- Improved: Documentation alignment with RLM paper's best practices.

## 0.7.0

- Added: CLI flags for sub-query configuration (`--sub-query-backend`,
  `--sub-query-timeout`, `--sub-query-share-session`).
- Added: Runtime `configure` MCP tool and REPL helpers (`set_backend`,
  `get_config`) for sub-query config.
- Added: `ALEPH_SUB_QUERY_TIMEOUT` environment variable to align CLI/API
  sub-query timeouts.
- Fixed: Validation retry behavior respects per-call settings over env defaults.
- Improved: Sub-query error messages now include allowed backend choices.

## 0.6.0

- Fixed: Workspace root auto-detection to honor `ALEPH_WORKSPACE_ROOT` and
  prefer invocation directories (`PWD`/`INIT_CWD`) before falling back to
  `os.getcwd()`.

## 0.5.9

- Fixed: `sub_query` to auto-inject session context when `context_slice` is
  omitted.
- Added: Shared session support for CLI sub-agents (codex/gemini/claude) via
  streamable HTTP.
- Changed: Deprioritized `claude` CLI backend (hangs in MCP/sandbox contexts);
  new order: api -> codex -> gemini -> claude.
- Fixed: stdin handling in CLI backends to prevent subprocess from stealing
  MCP stdio.

## 0.5.8

- Added: Smart loaders for PDF/DOCX/HTML and compressed logs (.gz/.bz2/.xz)
  in `load_file`.
- Added: Fast repo-wide search via `rg_search` and lightweight
  `semantic_search` + `embed_text` helpers.
- Added: Task tracking per context and automatic memory pack save/load.
- Improved: Provenance defaults (peek records evidence) and extended default
  timeouts.

## 0.5.7

- Changed: Codex CLI sub-queries now use `codex exec --full-auto`, with stdin
  support for long prompts.
- Added: Auto-reconnect for remote MCP servers and a configurable default
  timeout (`ALEPH_REMOTE_TOOL_TIMEOUT`).

## 0.5.6

- Removed: Deprecated recipe workflow and aider backend references.
- Added: Gemini CLI sub-query backend and updated backend priority docs.
- Improved: Sub-query system prompt for structured output.
- Added: Full Power Mode docs and made installer defaults max power.
- Added: `--max-write-bytes` and aligned file size limits across docs.
- Clarified: Action-tool file size caps and workspace mode usage.

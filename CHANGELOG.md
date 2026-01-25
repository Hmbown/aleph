# Changelog

## 0.7.4

- New: `alef` CLI command for running full RLM loop without MCP server.
- New: CLI provider (`--provider cli`) for API-key-free operation via `claude`, `codex`, or `gemini` CLIs.
- New: Entry point `alef run "prompt" --provider cli --model claude` with context file/stdin support.
- Fixed: Code blocks before FINAL directives are now executed properly (parser priority fix).
- Fixed: Trajectory JSON serialization for `ActionType` enum.
- Fixed: Type annotations for mypy compliance.

## 0.7.3

- New: CLI recursion tracking and depth controls for `sub_aleph`.
- Improved: Session management for nested recursion contexts.

## 0.7.2

- New: `sub_aleph` nested recursion tool for RLM-style recursive reasoning with depth control.
- New: MCP and REPL exposure for `sub_aleph` with configurable `max_depth`, `max_iterations`, and `max_sub_queries`.
- New: `ALEPH_MAX_DEPTH` environment variable for limiting recursion depth.
- New: Double recursion test (`tests/test_double_recursion.py`) for deterministic verification.
- Updated docs for `sub_aleph` usage patterns and depth configuration.

## 0.7.1

- Enhanced system prompt with RLM paper examples (arXiv:2512.24601 Appendix D patterns).
- Added sub-query batching efficiency guidance: ~100-200K chars per call, avoid 1000s of small calls.
- New /aleph skill examples: iterative document analysis, regex-targeted sub-queries, answer verification pattern.
- Improved documentation alignment with RLM paper's best practices.

## 0.7.0

- New: CLI flags for sub-query configuration (`--sub-query-backend`, `--sub-query-timeout`, `--sub-query-share-session`).
- New: Runtime `configure` MCP tool and REPL helpers (`set_backend`, `get_config`) for sub-query config.
- New: `ALEPH_SUB_QUERY_TIMEOUT` environment variable to align CLI/API sub-query timeouts.
- Fixed: Validation retry behavior respects per-call settings over env defaults.
- Improved: Sub-query error messages now include allowed backend choices.

## 0.6.0

- Fixed workspace root auto-detection to honor `ALEPH_WORKSPACE_ROOT` and prefer invocation directories (`PWD`/`INIT_CWD`) before falling back to `os.getcwd()`.

## 0.5.9

- Fixed `sub_query` to auto-inject session context when `context_slice` is omitted.
- Added shared session support for CLI sub-agents (codex/gemini/claude) via streamable HTTP.
- Deprioritized `claude` CLI backend (hangs in MCP/sandbox contexts); new order: api → codex → gemini → claude.
- Fixed stdin handling in CLI backends to prevent subprocess from stealing MCP stdio.

## 0.5.8

- Added smart loaders for PDF/DOCX/HTML and compressed logs (.gz/.bz2/.xz) in `load_file`.
- Added fast repo-wide search via `rg_search` and lightweight `semantic_search` + `embed_text` helpers.
- Added task tracking per context and automatic memory pack save/load.
- Improved provenance defaults (peek records evidence) and extended default timeouts.

## 0.5.7

- Switched Codex CLI sub-queries to `codex exec --full-auto`, with stdin support for long prompts.
- Added auto-reconnect for remote MCP servers and a configurable default timeout (`ALEPH_REMOTE_TOOL_TIMEOUT`).

## 0.5.6

- Removed deprecated recipe workflow and aider backend references.
- Added Gemini CLI sub-query backend and updated backend priority docs.
- Improved sub-query system prompt for structured output.
- Added Full Power Mode docs and made installer defaults max power.
- Added `--max-write-bytes` and aligned file size limits across docs.
- Clarified action-tool file size caps and workspace mode usage.

# Aleph Context Isolation Notes

## Goal

Keep MCP tool responses small so model context window usage is bounded, while large data stays in Aleph in-memory sessions.

## Current Contract

- Raw session context (`ctx`) must stay server-side.
- `get_variable("ctx")` is blocked.
- `exec_python` output is bounded by sandbox caps and then MCP response caps.
- Query/navigation tools (`peek_context`, `search_context`, `semantic_search`, `diff_contexts`) are bounded at the MCP boundary.
- Persistence tools may serialize full context to disk, but user-facing tool responses must not return raw `ctx` payloads.

## Guardrails

- Default MCP response cap: `10_000` chars (`max_tool_response_chars`).
- Truncation marker: `... [TRUNCATED]`.
- Structured payload sanitizer redacts `ctx` fields and truncates oversized strings before formatting responses.
- Sub-query context embedding is explicit and bounded (`max_context_chars` default `20_000`).

## Verification Suite

Run:

```bash
pytest tests/test_context_isolation_e2e.py -v
```

The suite covers:

1. System prompt isolation.
2. Blocking `get_variable("ctx")`.
3. `exec_python` return-value truncation.
4. `exec_python` stdout truncation.
5. Bounded `peek_context`.
6. Bounded `search_context`.
7. Safe derived-variable retrieval path.
8. Bounded `semantic_search`.
9. Server-side computation with small retrieval.
10. `think` behavior without raw context echo.
11. Large variable truncation via `get_variable`.
12. Bounded `diff_contexts`.

All test responses are also checked for the 10K response bound.

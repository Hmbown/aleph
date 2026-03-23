# Fix Aleph to properly implement Recursive Language Models

## Reference

- Paper: `docs/2512.24601v1.pdf` (Zhang, Kraska, Khattab, 2025)
- Official implementation: https://github.com/alexzhang13/rlm
- Author article: https://towardsdatascience.com/recursive-language-models-new-rules-for-agentic-ai/

Read the paper first. Read `docs/2512.24601v1.pdf`. Understand what an RLM actually is before touching any code.

## What an RLM actually is (from the paper, section 3)

An RLM is **a language model that generates and executes Python code in a REPL**. The key mechanism:

1. The environment (REPL) holds observations as variables (e.g., `ctx`).
2. The LLM generates a Python code block.
3. The environment executes the code and returns the result (an "observation").
4. The LLM sees the observation and generates the next code block.
5. This repeats until the LLM calls a `finish()` function.

The paper's central insight: **by giving the LLM a programmatic execution environment (the REPL), the model can structure its own reasoning deterministically through code rather than relying on free-form text generation.** This is similar to DSPy's philosophy — programmatic, composable, inspectable reasoning steps.

The REPL is NOT a convenience feature. It IS the mechanism. The LLM doesn't "also have a REPL." The LLM operates IN the REPL. Code generation is the primary action, not a side effect.

Sub-model calls happen THROUGH the REPL as Python function calls (`subquery()`), not through external CLI tool invocations.

## What Aleph currently gets wrong

### Problem 1: README and docs frame RLM as "load large files into RAM"

The current README opens with: "turns your coding agent into an RLM. It keeps large repos, logs, and documents in a Python process instead of the model prompt."

This is backwards. The paper's contribution is NOT about external memory for large files. External memory is a prerequisite that enables the actual mechanism: **the LLM writes code to reason**. The framing should be:

- Primary: "An RLM is a language model that writes Python code to reason through problems. Aleph provides the REPL environment where that code runs."
- Secondary: "Because the context lives in the REPL as a variable, not in the prompt, the model can work with data of any size."

Currently the README buries the REPL/code-execution mechanism and leads with the file-loading use case.

### Problem 2: The skill prompt (docs/prompts/aleph.md) is a file-analysis workflow, not an RLM prompt

The current `/aleph` skill is designed around: "load a file → search it → report findings." It should be designed around: "here is a problem and context → write code to reason about it → iterate until solved."

The skill prompt should teach the model to:
- Write Python code blocks as its PRIMARY action
- Use the REPL helpers (search, chunk, lines, sub_query) as FUNCTIONS CALLED FROM CODE, not as MCP tools called directly
- Maintain state across turns using variables in the REPL namespace
- Think of each iteration as a step in a program, not a conversation

### Problem 3: MCP tools are exposed as if they're the main interface

The MCP tool layer (load_file, search_context, peek_context, exec_python) is presented as the primary way to use Aleph. In an RLM:
- `exec_python` is the PRIMARY tool. Everything else is secondary.
- The model should spend 90%+ of its time in `exec_python` blocks
- `search_context`, `peek_context` etc. are convenience wrappers, but the model should prefer doing searches/analysis INSIDE exec_python using the REPL helpers (search(), lines(), chunk(), etc.)

### Problem 4: Sub-queries are implemented as CLI tool invocations, not REPL function calls

The current sub_query implementation spawns external CLI processes (codex, claude, gemini, kimi, opencode). In the paper, sub-models are called through the REPL via `subquery()`. The CLI backend layer is a platform feature, not an RLM feature.

The core RLM should support `sub_query()` as a Python function call within the REPL that invokes the configured provider directly. The CLI backends are an extension mechanism for when you want to delegate to a different tool entirely.

### Problem 5: The "code execution is the action" framing is weak

The system prompt and documentation don't emphasize enough that **code generation is the primary loop action**. The model should default to writing code, not to calling MCP tools or asking clarifying questions. The `FINAL(...)` protocol exists to terminate the loop, but the loop body is CODE.

## What you need to fix

### Fix 1: README.md

Rewrite the README to lead with the RLM mechanism:

1. Opening paragraph should explain: Aleph implements Recursive Language Models — a paradigm where an LLM writes and executes Python code in a REPL to reason through problems programmatically.
2. The diagram should show the REPL loop, not just "LLM → Aleph → results."
3. The "Why Aleph" section should lead with: "The model writes code instead of generating free-form text" and "Reasoning steps are deterministic, inspectable, and composable."
4. File loading / large context should be positioned as one benefit of the REPL architecture, not the primary feature.
5. Keep the quick start, common workloads, and other practical sections, but reframe them around the REPL-first model.

### Fix 2: docs/prompts/aleph.md (the skill prompt)

Rewrite the skill prompt so the model understands it is operating as an RLM:

1. The model's PRIMARY action is to write ```python code blocks
2. The REPL has `ctx` as the context variable, plus helpers: `search()`, `lines()`, `chunk()`, `sub_query()`, `sub_aleph()`, `peek()`, `cite()`, etc.
3. Each code block is an iteration. Maintain state using variables across iterations.
4. Do NOT call MCP tools directly (search_context, peek_context) when you can do the same thing with REPL helpers inside exec_python.
5. The loop ends when you have enough information to write `FINAL(answer)`.
6. Show concrete examples of the RLM loop: iteration 1 → write code to search → iteration 2 → write code to filter results → iteration 3 → write code to compute answer → FINAL(answer)

### Fix 3: System prompt (aleph/prompts/system.py)

Read the current system prompt. Rewrite it to:
1. Explicitly state that the model is operating as an RLM
2. Emphasize that code generation is the primary action
3. Show the iteration pattern clearly
4. Reduce emphasis on calling MCP tools and increase emphasis on writing code that uses REPL helpers

### Fix 4: Clarify the two modes in documentation

There are two ways to use Aleph, and the docs should be explicit about this:

**Mode 1: RLM Core Loop** (`aleph run`, `Aleph.complete()`, the `/aleph` skill)
- The LLM writes Python code in a REPL
- Context is a variable in the REPL
- Sub-models are called via `sub_query()` from within code
- This is the paper-aligned behavior

**Mode 2: MCP Tool Server** (using Aleph as an MCP server from Claude Code, Cursor, etc.)
- The host LLM calls Aleph tools (load_file, search_context, exec_python)
- The host LLM is NOT operating as an RLM — it's a tool-calling agent using Aleph as a tool
- This is useful but is NOT the RLM paradigm

The current docs conflate these two modes. Separate them clearly.

### Fix 5: Core sub_query architecture

The REPL's `sub_query()` function should work as follows:
- When called from Python code in the REPL, it invokes a sub-model with the given prompt and context
- The default path should be a direct provider call (API), not a CLI subprocess
- CLI backends (codex, claude, etc.) are available but should be documented as extensions, not the default

Check the current implementation in `aleph/sub_query/` and `aleph/repl/sandbox.py`. The `sub_query()` function injected into the REPL currently tries to use CLI backends first. The priority should be:
1. Direct API call (same provider as root model, or configured sub-model)
2. CLI backends as fallback/override

## Constraints

- Do NOT break existing tests. Run `pytest tests/ -v` after changes.
- Do NOT remove CLI backend support — it's a useful platform feature.
- Do NOT change the core RLM loop logic in `aleph/core.py` unless specifically fixing the sub_query provider priority.
- Do NOT add new dependencies.
- Keep changes minimal and focused. This is a reframing + prompt fix, not a rewrite.
- Every change should make the codebase MORE aligned with the paper, not just different.
- The paper is the source of truth. If the paper says something and the code does something else, the code is wrong.

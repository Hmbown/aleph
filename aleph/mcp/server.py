"""MCP server exposing Aleph REPL tools.

This server exposes a small set of tools:
- load_context
- peek_context
- search_context
- exec_python
- sub_query
- get_variable

It is intentionally stateless outside of the in-memory session dictionary.

Install MCP support with:
    pip install aleph[mcp]

Then run:
    python -m aleph.mcp.server
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Literal, cast

from ..providers.registry import get_provider
from ..providers.base import LLMProvider, ProviderError
from ..repl.sandbox import REPLEnvironment, SandboxConfig
from ..types import ContentFormat, ContextMetadata


LineNumberBase = Literal[0, 1]
DEFAULT_LINE_NUMBER_BASE: LineNumberBase = 1


def _detect_format(text: str) -> ContentFormat:
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        try:
            import json

            json.loads(text)
            return ContentFormat.JSON
        except Exception:
            return ContentFormat.TEXT
    return ContentFormat.TEXT


def _analyze_text_context(text: str, fmt: ContentFormat) -> ContextMetadata:
    return ContextMetadata(
        format=fmt,
        size_bytes=len(text.encode("utf-8", errors="ignore")),
        size_chars=len(text),
        size_lines=text.count("\n") + 1,
        size_tokens_estimate=len(text) // 4,
        structure_hint=None,
        sample_preview=text[:500],
    )


def _validate_line_number_base(value: int) -> LineNumberBase:
    if value not in (0, 1):
        raise ValueError("line_number_base must be 0 or 1")
    return cast(LineNumberBase, value)


@dataclass(slots=True)
class _Session:
    repl: REPLEnvironment
    meta: ContextMetadata
    line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE


class AlephMCPServer:
    """MCP server wrapping Aleph-style REPL sessions."""

    def __init__(
        self,
        provider: LLMProvider | str = "anthropic",
        model: str = "claude-sonnet-4-20250514",
        sandbox_config: SandboxConfig | None = None,
        mode: Literal["tools"] = "tools",
    ) -> None:
        self.provider = get_provider(provider) if isinstance(provider, str) else provider
        self.model = model
        self.mode = mode
        self.sandbox_config = sandbox_config or SandboxConfig()

        self._sessions: dict[str, _Session] = {}

        # Import MCP lazily so it's an optional dependency.
        try:
            from mcp.server.fastmcp import FastMCP
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "MCP support requires the `mcp` package. Install with `pip install aleph[mcp]`."
            ) from e

        self.server = FastMCP("aleph")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.tool()
        async def load_context(
            context: str,
            context_id: str = "default",
            format: str = "auto",
            line_number_base: LineNumberBase = DEFAULT_LINE_NUMBER_BASE,
        ) -> str:
            """Load context into an in-memory REPL session."""

            try:
                base = _validate_line_number_base(line_number_base)
            except ValueError as e:
                return f"Error: {e}"

            fmt = _detect_format(context) if format == "auto" else ContentFormat(format)
            meta = _analyze_text_context(context, fmt)

            repl = REPLEnvironment(
                context=context,
                context_var_name="ctx",
                config=self.sandbox_config,
                loop=asyncio.get_running_loop(),
            )
            repl.set_variable("line_number_base", base)

            # Provide a lightweight sub_query function inside the REPL for convenience.
            async def _sub_query(prompt: str, context_slice: str | None = None) -> str:
                messages = [{"role": "user", "content": prompt}]
                if context_slice:
                    messages[0]["content"] = f"{prompt}\n\nContext:\n{context_slice}"

                text, *_ = await self.provider.complete(messages=messages, model=self.model, max_tokens=4096)
                return text

            repl.inject_sub_query(_sub_query)

            self._sessions[context_id] = _Session(repl=repl, meta=meta, line_number_base=base)
            return (
                f"Loaded context '{context_id}': {meta.size_chars:,} chars, {meta.size_lines:,} lines, "
                f"~{meta.size_tokens_estimate:,} tokens (line numbers { '1-based' if base == 1 else '0-based' })"
            )

        @self.server.tool()
        async def peek_context(
            start: int = 0,
            end: int | None = None,
            context_id: str = "default",
            unit: Literal["chars", "lines"] = "chars",
        ) -> str:
            """Return a slice of the loaded context by chars or lines."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]
            repl = session.repl
            if unit == "chars":
                fn = repl.get_variable("peek")
                if not callable(fn):
                    return "Error: peek() helper is not available"
                peek_fn = cast(Callable[[int, int | None], str], fn)
                return peek_fn(start, end)
            fn = repl.get_variable("lines")
            if not callable(fn):
                return "Error: lines() helper is not available"
            lines_fn = cast(Callable[[int, int | None], str], fn)
            base = session.line_number_base
            if base == 1 and start == 0:
                start = 1
            if end == 0 and base == 1:
                end = 1
            if start < base:
                return f"Error: start must be >= {base} for line-based peeks"
            if end is not None and end < start:
                return "Error: end must be >= start"
            start_idx = start - base
            end_idx = None if end is None else end - base + 1
            return lines_fn(start_idx, end_idx)

        @self.server.tool()
        async def search_context(
            pattern: str,
            context_id: str = "default",
            max_results: int = 10,
            context_lines: int = 2,
        ) -> str:
            """Regex search the loaded context and return matches with surrounding context."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            session = self._sessions[context_id]
            repl = session.repl
            fn = repl.get_variable("search")
            if not callable(fn):
                return "Error: search() helper is not available"
            results = fn(pattern, context_lines=context_lines, max_results=max_results)

            if not results:
                return "No matches found."

            out: list[str] = []
            for r in results:
                try:
                    line_num = r["line_num"] + session.line_number_base
                    out.append(f"Line {line_num}:\n{r['context']}")
                except Exception:
                    out.append(str(r))
            return "\n---\n".join(out)

        @self.server.tool()
        async def exec_python(code: str, context_id: str = "default") -> str:
            """Execute Python code in the sandbox REPL for a given context session."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            repl = self._sessions[context_id].repl
            result = await repl.execute_async(code)

            out = result.stdout
            if result.stderr:
                out += f"\n[STDERR]: {result.stderr}"
            if result.error:
                out += f"\n[ERROR]: {result.error}"
            if result.return_value is not None:
                out += f"\n[RETURN_VALUE]: {result.return_value}"
            return out or "(no output)"

        @self.server.tool()
        async def sub_query(
            prompt: str,
            context_slice: str | None = None,
            context_id: str = "default",
        ) -> str:
            """Run a lightweight provider call (sub-query) with optional context slice."""
            messages = [{"role": "user", "content": prompt}]
            if context_slice:
                messages[0]["content"] = f"{prompt}\n\nContext:\n{context_slice}"

            try:
                text, *_ = await self.provider.complete(messages=messages, model=self.model, max_tokens=4096)
                return text
            except ProviderError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error: {e}"

        @self.server.tool()
        async def get_variable(name: str, context_id: str = "default") -> str:
            """Return a variable from the sandbox REPL namespace as a string."""
            if context_id not in self._sessions:
                return f"Error: No context loaded with ID '{context_id}'. Use load_context first."

            value = self._sessions[context_id].repl.get_variable(name)
            if value is None:
                return f"Variable '{name}' not found"
            return str(value)

    async def run(self, transport: str = "stdio") -> None:
        if transport != "stdio":
            raise ValueError("Only stdio transport is supported in Aleph v1")

        await self.server.run_stdio_async()


def main() -> None:
    """CLI entry point: `python -m aleph.mcp.server`"""

    import argparse

    parser = argparse.ArgumentParser(description="Run Aleph as an MCP server (stdio transport)")
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"], help="LLM provider")
    parser.add_argument("--model", default=None, help="Model name")

    args = parser.parse_args()

    model = args.model or ("claude-sonnet-4-20250514" if args.provider == "anthropic" else "gpt-4o")

    server = AlephMCPServer(provider=args.provider, model=model)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()

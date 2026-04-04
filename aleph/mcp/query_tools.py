"""Query MCP tool registrations for the local server."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Literal

from .session import _Evidence

if TYPE_CHECKING:
    from .local_server import AlephMCPServerLocal


def register_query_tools(
    owner: "AlephMCPServerLocal",
    *,
    get_repl_helper: Callable[[Any, str], Any],
    to_internal_line_index: Callable[[int | None, int], int | None],
) -> None:
    _tool = owner._tool_decorator

    @_tool()
    async def peek_context(
        start: int = 0,
        end: int | None = None,
        unit: Literal["chars", "lines"] = "chars",
        record_evidence: bool = False,
        context_id: str = "default",
    ) -> str:
        """View a portion of the loaded context."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        repl = session.repl
        session.iterations += 1

        fn = get_repl_helper(repl, "peek") if unit == "chars" else get_repl_helper(repl, "lines")
        if not callable(fn):
            return f"Error: {unit} helper is not available"

        try:
            if unit == "lines":
                if session.line_number_base == 1 and start == 0:
                    start_idx = 0
                    end_idx = end
                else:
                    start_idx = to_internal_line_index(start, session.line_number_base)
                    end_idx = to_internal_line_index(end, session.line_number_base)
                res = fn(start_idx, end_idx)
            else:
                res = fn(start, end)
        except Exception as exc:
            return f"Error: {exc}"

        if record_evidence and res:
            line_range = None
            if unit == "lines":
                line_range = (start, end if end is not None else session.meta.size_lines)
            session.evidence.append(
                _Evidence(
                    source="peek",
                    line_range=line_range,
                    pattern=None,
                    note=f"peek {unit} {start}:{end}",
                    snippet=str(res)[:200],
                )
            )

        res_text, _ = owner._truncate_tool_text(str(res))
        return res_text

    @_tool()
    async def search_context(
        pattern: str,
        context_id: str = "default",
        context_lines: int = 2,
        max_results: int = 10,
        record_evidence: bool = True,
        evidence_mode: Literal["summary", "all"] = "summary",
    ) -> str:
        """Search the context using regex patterns."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        repl = session.repl
        session.iterations += 1

        fn = get_repl_helper(repl, "search")
        if not callable(fn):
            return "Error: search() helper is not available"

        try:
            results = fn(pattern, context_lines=context_lines, max_results=max_results)
        except Exception as exc:
            return f"Error: {exc}"

        if not isinstance(results, list):
            return f"Error: search() returned unexpected type {type(results)}"

        if record_evidence and results:
            if evidence_mode == "summary":
                session.evidence.append(
                    _Evidence(
                        source="search",
                        line_range=None,
                        pattern=pattern,
                        note=f"{len(results)} match(es) (summary)",
                        snippet=str(results[0].get("match", ""))[:200] if results else "",
                    )
                )
            else:
                for result in results:
                    if isinstance(result, dict):
                        line_no = int(result.get("line_num", 0))
                        session.evidence.append(
                            _Evidence(
                                source="search",
                                line_range=(line_no, line_no),
                                pattern=pattern,
                                note="match",
                                snippet=str(result.get("match", ""))[:200],
                            )
                        )

        if not results:
            return f"No matches found for `{pattern}`."

        shown_results, hits_truncated = owner._limit_json_items(
            results,
            max_chars=owner.max_tool_response_chars,
        )
        res = [f"## Search Results for `{pattern}`\n"]
        res.append(
            f"Found {len(results)} match(es) (line numbers are {session.line_number_base}-based):\n"
        )
        if hits_truncated:
            res.append(
                f"Showing first {len(shown_results)} result(s) due to response size limit.\n"
            )
        for result in shown_results:
            if isinstance(result, dict):
                res.append(f"**Line {result.get('line_num')}:**")
                res.append(f"```\n{result.get('context')}\n```")
        text, _ = owner._truncate_tool_text("\n".join(res))
        return text

    @_tool()
    async def semantic_search(
        query: str,
        context_id: str = "default",
        chunk_size: int = 1000,
        overlap: int = 100,
        top_k: int = 5,
        embed_dim: int = 256,
        record_evidence: bool = True,
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Semantic search over the context using lightweight embeddings."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        repl = session.repl
        session.iterations += 1

        fn = get_repl_helper(repl, "semantic_search")
        if not callable(fn):
            return "Error: semantic_search() helper is not available"

        try:
            results = fn(
                query,
                chunk_size=chunk_size,
                overlap=overlap,
                top_k=top_k,
                embed_dim=embed_dim,
            )
        except Exception as exc:
            return f"Error: {exc}"

        if not isinstance(results, list):
            return f"Error: semantic_search() returned unexpected type {type(results)}"

        if record_evidence and results:
            session.evidence.append(
                _Evidence(
                    source="search",
                    line_range=None,
                    pattern=None,
                    note=f"semantic search: {query}",
                    snippet=str(results[0].get("preview", ""))[:200] if results else "",
                )
            )

        shown_results, hits_truncated = owner._limit_json_items(
            results,
            max_chars=owner.max_tool_response_chars,
        )
        if output == "object":
            payload: dict[str, Any] = {"results": shown_results}
            if hits_truncated:
                payload["truncated"] = True
                payload["total_results"] = len(results)
            return payload
        if output == "json":
            payload = {"results": shown_results}
            if hits_truncated:
                payload["truncated"] = True
                payload["total_results"] = len(results)
            text, _ = owner._truncate_tool_text(json.dumps(payload, indent=2))
            return text

        if not results:
            return f"No semantic matches found for `{query}`."

        res = [f"## Semantic Results for `{query}`\n"]
        if hits_truncated:
            res.append(
                f"Showing first {len(shown_results)} result(s) due to response size limit.\n"
            )
        for result in shown_results:
            if isinstance(result, dict):
                score = result.get("score", 0.0)
                text = result.get("preview", "")
                res.append(f"### Score: {score:.4f}")
                res.append(f"```\n{text}\n```")
        text, _ = owner._truncate_tool_text("\n".join(res))
        return text

    @_tool()
    async def exec_python(
        code: str,
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        """Execute Python code in the sandboxed REPL."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        repl = session.repl
        session.iterations += 1

        try:
            result = await repl.execute_async(code)
        except Exception as exc:
            return f"Error: {exc}"

        return owner._format_execution_result(result)

    @_tool()
    async def exec_javascript(
        code: str,
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        """Execute JavaScript code in the persistent Node.js REPL."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        session.iterations += 1

        try:
            repl = owner._get_or_create_node_repl(context_id)
            result = await repl.execute_async(code, language="javascript")
            citations = owner._sync_session_from_node_repl(context_id)
        except Exception as exc:
            return f"Error: {exc}"

        for citation in citations:
            session.evidence.append(
                _Evidence(
                    source="exec",
                    line_range=tuple(citation["line_range"]) if citation.get("line_range") else None,
                    pattern=None,
                    note=citation.get("note"),
                    snippet=str(citation.get("snippet", ""))[:200],
                )
            )

        return owner._format_execution_result(result)

    @_tool()
    async def exec_typescript(
        code: str,
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        """Execute TypeScript code in the persistent Node.js REPL."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        session.iterations += 1

        try:
            repl = owner._get_or_create_node_repl(context_id)
            result = await repl.execute_async(code, language="typescript")
            citations = owner._sync_session_from_node_repl(context_id)
        except Exception as exc:
            return f"Error: {exc}"

        for citation in citations:
            session.evidence.append(
                _Evidence(
                    source="exec",
                    line_range=tuple(citation["line_range"]) if citation.get("line_range") else None,
                    pattern=None,
                    note=citation.get("note"),
                    snippet=str(citation.get("snippet", ""))[:200],
                )
            )

        return owner._format_execution_result(result)

    @_tool()
    async def get_variable(
        name: str,
        context_id: str = "default",
        language: Literal["python", "javascript", "typescript"] = "python",
    ) -> Any:
        """Retrieve a variable from the REPL namespace."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."
        if name == "ctx" and owner.context_policy == "isolated":
            return (
                f"Blocked: get_variable('{name}') is restricted under isolated policy.\n"
                "Alternatives:\n"
                "  - Use exec_python(code='result = len(ctx)') then get_variable('result')\n"
                "  - Use exec_javascript(code='const result = ctx.length') then get_variable('result', language='javascript')\n"
                "  - Use peek_context() to view bounded ranges\n"
                "  - Use search_context() to find specific patterns\n"
                "Tip: switch to trusted policy via configure(context_policy='trusted') if appropriate."
            )

        session = owner._sessions[context_id]
        if language == "python":
            value = session.repl.get_variable(name)
        else:
            try:
                value = owner._get_or_create_node_repl(context_id).get_variable(name)
            except Exception as exc:
                return f"Error: {exc}"
        return owner._format_variable_value(name, value)

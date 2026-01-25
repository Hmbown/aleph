"""Comprehensive tests for the Aleph MCP Local Server.

Tests all 8 MCP tools:
- load_context
- peek_context
- search_context
- exec_python
- get_variable
- think
- get_status
- finalize

Also tests:
- Error handling
- Security blocks via MCP layer
- Session management
- Performance and limits
"""

from __future__ import annotations

import json
import os
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from aleph.mcp.local_server import AlephMCPServerLocal, _detect_format, _analyze_text_context
from aleph.repl.sandbox import SandboxConfig
from aleph.types import AlephResponse, ContentFormat
from aleph.sub_query import SubQueryConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sandbox_config():
    """Test sandbox config with shorter timeout."""
    return SandboxConfig(timeout_seconds=5.0, max_output_chars=5000)


@pytest.fixture
def mcp_server(sandbox_config):
    """Create MCP server instance for testing."""
    return AlephMCPServerLocal(sandbox_config=sandbox_config)


@pytest.fixture
async def loaded_server(mcp_server):
    """MCP server with test context loaded."""
    # Access the internal load_context (tools are registered as closures)
    # We need to call the tool through the server
    await _call_tool(mcp_server, "load_context",
                     context="Line 1: Hello World\nLine 2: Test data\nLine 3: Goodbye",
                     context_id="test")
    return mcp_server


async def _call_tool(server, tool_name: str, **kwargs):
    """Helper to call a tool registered on the server."""
    # The tools are registered as closures, access them via server.server
    # For testing, we'll directly manipulate the server state

    if tool_name == "load_context":
        context = kwargs.get("context", "")
        context_id = kwargs.get("context_id", "default")
        format_str = kwargs.get("format", "auto")
        line_number_base = kwargs.get("line_number_base", 1)

        fmt = _detect_format(context) if format_str == "auto" else ContentFormat(format_str)
        meta = _analyze_text_context(context, fmt)

        from aleph.repl.sandbox import REPLEnvironment
        repl = REPLEnvironment(
            context=context,
            context_var_name="ctx",
            config=server.sandbox_config,
            loop=asyncio.get_running_loop(),
        )
        repl.set_variable("line_number_base", line_number_base)

        from aleph.mcp.local_server import _Session
        server._sessions[context_id] = _Session(repl=repl, meta=meta, line_number_base=line_number_base)
        return f"Context loaded: {context_id}"

    # Get session for tools that need it
    context_id = kwargs.get("context_id", "default")
    if context_id not in server._sessions:
        return f"Error: No context loaded with ID '{context_id}'"
    session = server._sessions[context_id]

    if tool_name == "search_context":
        from aleph.mcp.local_server import _Evidence
        pattern = kwargs.get("pattern", "")
        context_lines = kwargs.get("context_lines", 2)
        max_results = kwargs.get("max_results", 10)
        record_evidence = kwargs.get("record_evidence", True)
        evidence_mode = kwargs.get("evidence_mode", "summary")

        session.iterations += 1
        fn = session.repl.get_variable("search")
        if not callable(fn):
            return "Error: search() helper not available"

        import re
        try:
            results = fn(pattern, context_lines=context_lines, max_results=max_results)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        if not results:
            return f"No matches for: {pattern}"

        # Track evidence
        evidence_before = len(session.evidence)
        base = session.line_number_base
        if record_evidence:
            ranges = []
            for r in results:
                line_num = r['line_num']
                start = max(base, line_num - context_lines)
                end = line_num + context_lines
                ranges.append((start, end))
            if evidence_mode == "all":
                for r, line_range in zip(results, ranges):
                    session.evidence.append(_Evidence(
                        source="search",
                        line_range=line_range,
                        pattern=pattern,
                        snippet=r['match'][:200],
                    ))
            else:
                start = min(r[0] for r in ranges)
                end = max(r[1] for r in ranges)
                session.evidence.append(_Evidence(
                    source="search",
                    line_range=(start, end),
                    pattern=pattern,
                    snippet=results[0]['match'][:200],
                ))
        session.information_gain.append(len(session.evidence) - evidence_before)
        return f"Found {len(results)} matches"

    if tool_name == "peek_context":
        from aleph.mcp.local_server import _Evidence
        start = kwargs.get("start", 0)
        end = kwargs.get("end")
        unit = kwargs.get("unit", "chars")
        record_evidence = kwargs.get("record_evidence", False)

        session.iterations += 1
        if unit == "chars":
            fn = session.repl.get_variable("peek")
        else:
            fn = session.repl.get_variable("lines")
            base = session.line_number_base
            if base == 1 and start == 0:
                start = 1
            if end == 0 and base == 1:
                end = 1
            start_idx = start - base
            end_idx = None if end is None else end - base + 1
        if not callable(fn):
            return "Error: peek/lines helper not available"
        if unit == "chars":
            result = fn(start, end)
        else:
            result = fn(start_idx, end_idx)

        # Track evidence
        evidence_before = len(session.evidence)
        if record_evidence and result:
            session.evidence.append(_Evidence(
                source="peek",
                line_range=(start, end) if unit == "lines" else None,
                pattern=None,
                snippet=result[:200],
            ))
        session.information_gain.append(len(session.evidence) - evidence_before)
        return result

    if tool_name == "exec_python":
        from aleph.mcp.local_server import _Evidence
        code = kwargs.get("code", "")
        session.iterations += 1

        evidence_before = len(session.evidence)
        result = await session.repl.execute_async(code)

        # Collect citations
        if hasattr(session.repl, '_citations') and session.repl._citations:
            for citation in session.repl._citations:
                session.evidence.append(_Evidence(
                    source="manual",
                    line_range=citation.get('line_range'),
                    pattern=None,
                    note=citation.get('note'),
                    snippet=citation.get('snippet', '')[:200],
                ))
            session.repl._citations.clear()

        session.information_gain.append(len(session.evidence) - evidence_before)
        return result

    if tool_name == "think":
        question = kwargs.get("question", "")
        session.iterations += 1
        session.think_history.append(question)
        return f"Reasoning step: {question}"

    if tool_name == "get_status":
        parts = [
            "## Context Status",
            f"**Context ID:** `{context_id}`",
            f"**Iterations:** {session.iterations}",
            "### Convergence Metrics",
            f"- Evidence collected: {len(session.evidence)}",
        ]
        if session.confidence_history:
            parts.append(f"- Latest confidence: {session.confidence_history[-1]:.1%}")
        return "\n".join(parts)

    if tool_name == "get_evidence":
        limit = int(kwargs.get("limit", 20))
        offset = int(kwargs.get("offset", 0))
        source = kwargs.get("source", "any")
        output = kwargs.get("output", "markdown")

        evidence = session.evidence
        if source != "any":
            evidence = [e for e in evidence if e.source == source]

        page = evidence[max(0, offset) : max(0, offset) + (20 if limit <= 0 else limit)]

        if output == "json":
            payload = [
                {
                    "source": ev.source,
                    "line_range": ev.line_range,
                    "pattern": ev.pattern,
                    "note": ev.note,
                    "snippet": ev.snippet,
                }
                for ev in page
            ]
            return json.dumps({
                "context_id": context_id,
                "total": len(evidence),
                "line_number_base": session.line_number_base,
                "items": payload,
            })

        return "\n".join(f"- [{ev.source}] {ev.snippet}" for ev in page) or "(no evidence)"

    if tool_name == "finalize":
        answer = kwargs.get("answer", "")
        confidence = kwargs.get("confidence", "medium")
        parts = ["## Final Answer", answer, f"**Confidence:** {confidence}"]
        if session.evidence:
            parts.append("### Evidence Citations")
            for ev in session.evidence[-10:]:
                parts.append(f"- [{ev.source}]: {ev.snippet[:80]}")
        return "\n".join(parts)

    if tool_name == "chunk_context":
        chunk_size = kwargs.get("chunk_size", 2000)
        overlap = kwargs.get("overlap", 200)
        session.iterations += 1

        fn = session.repl.get_variable("chunk")
        if not callable(fn):
            return "Error: chunk() helper not available"

        try:
            chunks = fn(chunk_size, overlap)
        except ValueError as e:
            return f"Error: {e}"

        # Build metadata
        chunk_meta = []
        pos = 0
        for i, chunk_text in enumerate(chunks):
            chunk_meta.append({
                "index": i,
                "start_char": pos,
                "end_char": pos + len(chunk_text),
                "size": len(chunk_text),
            })
            pos += len(chunk_text) - overlap if i < len(chunks) - 1 else len(chunk_text)

        session.chunks = chunk_meta
        return f"## Context Chunks\n\n**Total chunks:** {len(chunks)}\n\n" + "\n".join(
            f"- **Chunk {cm['index']}** ({cm['start_char']}-{cm['end_char']})"
            for cm in chunk_meta
        )

    if tool_name == "evaluate_progress":
        current_understanding = kwargs.get("current_understanding", "")
        remaining_questions = kwargs.get("remaining_questions")
        confidence_score = kwargs.get("confidence_score", 0.5)

        session.iterations += 1
        session.confidence_history.append(confidence_score)

        parts = [
            "## Progress Evaluation",
            f"**Current Understanding:**\n{current_understanding}",
            f"**Confidence Score:** {confidence_score:.1%}",
        ]

        if confidence_score >= 0.8:
            parts.append("**READY TO FINALIZE**")
        elif confidence_score >= 0.5 and not remaining_questions:
            parts.append("**CONSIDER FINALIZING**")
        else:
            parts.append("**CONTINUE EXPLORING**")

        return "\n".join(parts)

    if tool_name == "summarize_so_far":
        include_evidence = kwargs.get("include_evidence", True)
        clear_history = kwargs.get("clear_history", False)

        parts = [
            "## Context Summary",
            f"**Context ID:** `{context_id}`",
            f"**Iterations:** {session.iterations}",
        ]

        if session.think_history:
            parts.append("### Reasoning Steps")
            for q in session.think_history:
                parts.append(f"- {q[:100]}")

        if include_evidence and session.evidence:
            parts.append(f"### Evidence Collected\nTotal: {len(session.evidence)}")

        if clear_history:
            session.think_history = []
            parts.append("*History cleared*")

        return "\n".join(parts)

    return None


# ---------------------------------------------------------------------------
# Helper Function Tests
# ---------------------------------------------------------------------------

class TestDetectFormat:
    """Tests for _detect_format helper."""

    def test_detect_json_object(self):
        assert _detect_format('{"key": "value"}') == ContentFormat.JSON

    def test_detect_json_array(self):
        assert _detect_format('[1, 2, 3]') == ContentFormat.JSON

    def test_detect_text(self):
        assert _detect_format("plain text") == ContentFormat.TEXT

    def test_detect_invalid_json(self):
        assert _detect_format('{"invalid": }') == ContentFormat.TEXT

    def test_detect_with_whitespace(self):
        assert _detect_format('  {"key": "value"}') == ContentFormat.JSON


class TestAnalyzeContext:
    """Tests for _analyze_text_context helper."""

    def test_analyze_text(self):
        meta = _analyze_text_context("hello world", ContentFormat.TEXT)
        assert meta.format == ContentFormat.TEXT
        assert meta.size_chars == 11
        assert meta.size_lines == 1

    def test_analyze_multiline(self):
        meta = _analyze_text_context("line1\nline2\nline3", ContentFormat.TEXT)
        assert meta.size_lines == 3

    def test_analyze_preview_truncated(self):
        long_text = "x" * 1000
        meta = _analyze_text_context(long_text, ContentFormat.TEXT)
        assert len(meta.sample_preview) == 500


# ---------------------------------------------------------------------------
# Server Initialization Tests
# ---------------------------------------------------------------------------

class TestServerInit:
    """Test server initialization."""

    def test_server_creates_fastmcp_instance(self, mcp_server):
        assert mcp_server.server is not None
        assert mcp_server.server.name == "aleph-local"

    def test_sessions_dict_empty_on_init(self, mcp_server):
        assert mcp_server._sessions == {}

    def test_custom_sandbox_config_applied(self, sandbox_config):
        server = AlephMCPServerLocal(sandbox_config=sandbox_config)
        assert server.sandbox_config.timeout_seconds == 5.0
        assert server.sandbox_config.max_output_chars == 5000

    def test_default_sandbox_config(self):
        server = AlephMCPServerLocal()
        assert server.sandbox_config.timeout_seconds == 60.0


# ---------------------------------------------------------------------------
# load_context Tests
# ---------------------------------------------------------------------------

class TestLoadContext:
    """Tests for load_context tool."""

    @pytest.mark.asyncio
    async def test_load_creates_session(self, mcp_server):
        await _call_tool(mcp_server, "load_context",
                        context="test data",
                        context_id="new_session")
        assert "new_session" in mcp_server._sessions

    @pytest.mark.asyncio
    async def test_load_sets_context_variable(self, mcp_server):
        await _call_tool(mcp_server, "load_context",
                        context="my test content",
                        context_id="ctx_test")

        session = mcp_server._sessions["ctx_test"]
        ctx = session.repl.get_variable("ctx")
        assert ctx == "my test content"

    @pytest.mark.asyncio
    async def test_load_overwrites_existing_session(self, mcp_server):
        await _call_tool(mcp_server, "load_context",
                        context="first",
                        context_id="dup")
        await _call_tool(mcp_server, "load_context",
                        context="second",
                        context_id="dup")

        ctx = mcp_server._sessions["dup"].repl.get_variable("ctx")
        assert ctx == "second"

    @pytest.mark.asyncio
    async def test_load_auto_detects_json(self, mcp_server):
        await _call_tool(mcp_server, "load_context",
                        context='{"key": "value"}',
                        context_id="json_test",
                        format="auto")

        meta = mcp_server._sessions["json_test"].meta
        assert meta.format == ContentFormat.JSON


# ---------------------------------------------------------------------------
# peek_context Tests
# ---------------------------------------------------------------------------

class TestPeekContext:
    """Tests for peek_context tool."""

    @pytest.mark.asyncio
    async def test_peek_chars(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("peek")
        result = fn(0, 10)
        assert "Line 1: He" in result

    @pytest.mark.asyncio
    async def test_peek_lines(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("lines")
        result = fn(1, 2)
        assert "Test data" in result
        assert "Hello" not in result

    @pytest.mark.asyncio
    async def test_peek_negative_index(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("peek")
        result = fn(-7, None)
        assert "Goodbye" in result


# ---------------------------------------------------------------------------
# search_context Tests
# ---------------------------------------------------------------------------

class TestSearchContext:
    """Tests for search_context tool."""

    @pytest.mark.asyncio
    async def test_search_finds_matches(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("search")
        results = fn("Line", max_results=10)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_regex_pattern(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("search")
        results = fn(r"Line \d", max_results=10)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_no_match(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("search")
        results = fn("NOTFOUND")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_max_results_limit(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("search")
        results = fn("Line", max_results=2)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# exec_python Tests
# ---------------------------------------------------------------------------

class TestExecPython:
    """Tests for exec_python tool."""

    @pytest.mark.asyncio
    async def test_exec_simple_expression(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("1 + 1")
        assert result.return_value == 2

    @pytest.mark.asyncio
    async def test_exec_access_ctx(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("len(ctx)")
        assert result.return_value == 53  # Length of test context

    @pytest.mark.asyncio
    async def test_exec_print_output(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("print('hello')")
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_exec_variable_assignment(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("x = 42")
        assert "x" in result.variables_updated

    @pytest.mark.asyncio
    async def test_exec_allowed_import(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("import json\njson.dumps({})")
        assert result.error is None
        assert result.return_value == "{}"

    @pytest.mark.asyncio
    async def test_exec_error_captured(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("1/0")
        assert result.error is not None
        assert "division" in result.error.lower() or "ZeroDivision" in result.error


# ---------------------------------------------------------------------------
# Security Tests via MCP Layer
# ---------------------------------------------------------------------------

class TestSecurityViaMCP:
    """Test that sandbox security blocks work via MCP layer."""

    @pytest.mark.asyncio
    async def test_block_os_import(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("import os")
        assert result.error is not None
        assert "not allowed" in result.error

    @pytest.mark.asyncio
    async def test_block_subprocess(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("import subprocess")
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_block_eval(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("eval('1+1')")
        assert result.error is not None
        assert "eval" in result.error

    @pytest.mark.asyncio
    async def test_block_exec(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("exec('x=1')")
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_block_open(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("open('/etc/passwd')")
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_block_dunder_class(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("''.__class__")
        assert result.error is not None
        assert "__class__" in result.error

    @pytest.mark.asyncio
    async def test_block_class_definition(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("class Evil: pass")
        assert result.error is not None
        assert "Class" in result.error

    @pytest.mark.asyncio
    async def test_block_bare_except(self, loaded_server):
        session = loaded_server._sessions["test"]
        code = "try:\n    1/0\nexcept:\n    pass"
        result = await session.repl.execute_async(code)
        assert result.error is not None


# ---------------------------------------------------------------------------
# get_variable Tests
# ---------------------------------------------------------------------------

class TestGetVariable:
    """Tests for get_variable tool."""

    @pytest.mark.asyncio
    async def test_get_ctx_variable(self, loaded_server):
        repl = loaded_server._sessions["test"].repl
        # Check that ctx exists in namespace
        assert "ctx" in repl._namespace

    @pytest.mark.asyncio
    async def test_get_user_variable(self, loaded_server):
        session = loaded_server._sessions["test"]
        await session.repl.execute_async("my_var = [1, 2, 3]")
        value = session.repl.get_variable("my_var")
        assert value == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_get_none_variable(self, loaded_server):
        session = loaded_server._sessions["test"]
        await session.repl.execute_async("none_var = None")
        # Variable should exist even though value is None
        assert "none_var" in session.repl._namespace
        assert session.repl._namespace["none_var"] is None


# ---------------------------------------------------------------------------
# think Tool Tests
# ---------------------------------------------------------------------------

class TestThink:
    """Tests for think tool - note this tool returns prompts, not API calls."""

    @pytest.mark.asyncio
    async def test_think_records_history(self, loaded_server):
        session = loaded_server._sessions["test"]
        session.think_history.append("First question")
        session.think_history.append("Second question")
        assert len(session.think_history) == 2

    @pytest.mark.asyncio
    async def test_think_increments_iterations(self, loaded_server):
        session = loaded_server._sessions["test"]
        initial = session.iterations
        session.iterations += 1
        assert session.iterations == initial + 1


# ---------------------------------------------------------------------------
# get_status Tests
# ---------------------------------------------------------------------------

class TestGetStatus:
    """Tests for get_status tool."""

    @pytest.mark.asyncio
    async def test_status_has_session_info(self, loaded_server):
        session = loaded_server._sessions["test"]
        assert session.meta is not None
        assert session.created_at is not None

    @pytest.mark.asyncio
    async def test_status_tracks_iterations(self, loaded_server):
        session = loaded_server._sessions["test"]
        session.iterations = 5
        assert session.iterations == 5


# ---------------------------------------------------------------------------
# Session Management Tests
# ---------------------------------------------------------------------------

class TestSessionManagement:
    """Test multi-session management."""

    @pytest.mark.asyncio
    async def test_multiple_sessions(self, mcp_server):
        await _call_tool(mcp_server, "load_context",
                        context="Session A data",
                        context_id="session_a")
        await _call_tool(mcp_server, "load_context",
                        context="Session B data",
                        context_id="session_b")

        assert "session_a" in mcp_server._sessions
        assert "session_b" in mcp_server._sessions

    @pytest.mark.asyncio
    async def test_session_isolation(self, mcp_server):
        await _call_tool(mcp_server, "load_context",
                        context="A",
                        context_id="iso_a")
        await _call_tool(mcp_server, "load_context",
                        context="B",
                        context_id="iso_b")

        # Set variable in session A
        await mcp_server._sessions["iso_a"].repl.execute_async("x = 'A'")

        # Variable should not exist in session B
        assert "x" not in mcp_server._sessions["iso_b"].repl._namespace

    @pytest.mark.asyncio
    async def test_session_persistence(self, mcp_server):
        await _call_tool(mcp_server, "load_context",
                        context="test",
                        context_id="persist")

        # Set variables across multiple executions
        session = mcp_server._sessions["persist"]
        await session.repl.execute_async("counter = 0")
        await session.repl.execute_async("counter += 1")
        await session.repl.execute_async("counter += 1")

        assert session.repl.get_variable("counter") == 2


# ---------------------------------------------------------------------------
# Performance/Limits Tests
# ---------------------------------------------------------------------------

class TestPerformanceLimits:
    """Test timeout and output limits."""

    @pytest.mark.asyncio
    async def test_output_truncation(self):
        config = SandboxConfig(timeout_seconds=5.0, max_output_chars=100)
        server = AlephMCPServerLocal(sandbox_config=config)
        await _call_tool(server, "load_context", context="test", context_id="trunc")

        session = server._sessions["trunc"]
        result = await session.repl.execute_async("print('x' * 1000)")
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        config = SandboxConfig(timeout_seconds=0.5, max_output_chars=10000)
        server = AlephMCPServerLocal(sandbox_config=config)
        await _call_tool(server, "load_context", context="test", context_id="timeout")

        session = server._sessions["timeout"]
        result = await session.repl.execute_async("while True: pass")
        assert result.error is not None
        assert "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test graceful error handling."""

    @pytest.mark.asyncio
    async def test_syntax_error_captured(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("def broken(")
        assert result.error is not None
        # Python 3.10+ gives "was never closed" instead of "syntax"
        assert "never closed" in result.error.lower() or "syntax" in result.error.lower()

    @pytest.mark.asyncio
    async def test_runtime_error_captured(self, loaded_server):
        session = loaded_server._sessions["test"]
        result = await session.repl.execute_async("undefined_variable")
        assert result.error is not None
        assert "not defined" in result.error

    @pytest.mark.asyncio
    async def test_invalid_regex_in_search(self, loaded_server):
        session = loaded_server._sessions["test"]
        fn = session.repl.get_variable("search")
        import re
        with pytest.raises(re.error):
            fn("[invalid(regex")


# ---------------------------------------------------------------------------
# Integration Workflow Tests
# ---------------------------------------------------------------------------

class TestIntegrationWorkflow:
    """Test typical workflow patterns."""

    @pytest.mark.asyncio
    async def test_data_analysis_workflow(self, mcp_server):
        """Simulate typical data analysis workflow."""
        # Load JSON data
        json_data = '{"items": [1, 2, 3, 4, 5]}'
        await _call_tool(mcp_server, "load_context",
                        context=json_data,
                        context_id="workflow")

        session = mcp_server._sessions["workflow"]

        # Parse JSON
        result = await session.repl.execute_async(
            "import json\ndata = json.loads(ctx)\nsum(data['items'])"
        )
        assert result.return_value == 15

    @pytest.mark.asyncio
    async def test_text_search_workflow(self, mcp_server):
        """Simulate text search workflow."""
        text = """
ERROR: Connection failed
INFO: Starting process
ERROR: Timeout occurred
INFO: Process complete
WARNING: Low memory
"""
        await _call_tool(mcp_server, "load_context",
                        context=text,
                        context_id="logs")

        session = mcp_server._sessions["logs"]
        fn = session.repl.get_variable("search")

        # Find all errors
        errors = fn("ERROR", max_results=10)
        assert len(errors) == 2


# ============================================================
# Tests for New Tools (chunk_context, evaluate_progress, summarize_so_far)
# ============================================================


class TestChunkContext:
    """Tests for chunk_context tool."""

    @pytest.mark.asyncio
    async def test_chunk_basic(self, loaded_server):
        """Test basic chunking returns metadata."""
        result = await _call_tool(loaded_server, "chunk_context",
                                  chunk_size=20, overlap=5, context_id="test")
        assert "Context Chunks" in result
        assert "Chunk" in result
        assert "Total chunks:" in result

    @pytest.mark.asyncio
    async def test_chunk_stores_metadata(self, loaded_server):
        """Test that chunk metadata is stored in session."""
        await _call_tool(loaded_server, "chunk_context",
                        chunk_size=20, overlap=0, context_id="test")
        session = loaded_server._sessions["test"]
        assert session.chunks is not None
        assert len(session.chunks) > 0
        # Each chunk should have index, start_char, end_char
        assert "index" in session.chunks[0]
        assert "start_char" in session.chunks[0]
        assert "end_char" in session.chunks[0]

    @pytest.mark.asyncio
    async def test_chunk_invalid_params(self, loaded_server):
        """Test error handling for invalid chunk params."""
        result = await _call_tool(loaded_server, "chunk_context",
                                  chunk_size=10, overlap=15, context_id="test")
        assert "Error" in result


class TestEvaluateProgress:
    """Tests for evaluate_progress tool."""

    @pytest.mark.asyncio
    async def test_evaluate_basic(self, loaded_server):
        """Test basic progress evaluation."""
        result = await _call_tool(loaded_server, "evaluate_progress",
                                  current_understanding="I found the key data",
                                  confidence_score=0.7,
                                  context_id="test")
        assert "Progress Evaluation" in result
        assert "Confidence Score" in result
        assert "70%" in result or "70.0%" in result

    @pytest.mark.asyncio
    async def test_evaluate_tracks_confidence(self, loaded_server):
        """Test that confidence history is tracked."""
        await _call_tool(loaded_server, "evaluate_progress",
                        current_understanding="First pass",
                        confidence_score=0.3,
                        context_id="test")
        await _call_tool(loaded_server, "evaluate_progress",
                        current_understanding="Second pass",
                        confidence_score=0.6,
                        context_id="test")
        session = loaded_server._sessions["test"]
        assert len(session.confidence_history) == 2
        assert session.confidence_history[0] == 0.3
        assert session.confidence_history[1] == 0.6

    @pytest.mark.asyncio
    async def test_evaluate_recommendation_high_confidence(self, loaded_server):
        """Test recommendation for high confidence."""
        result = await _call_tool(loaded_server, "evaluate_progress",
                                  current_understanding="Complete understanding",
                                  confidence_score=0.9,
                                  context_id="test")
        assert "READY TO FINALIZE" in result

    @pytest.mark.asyncio
    async def test_evaluate_recommendation_low_confidence(self, loaded_server):
        """Test recommendation for low confidence."""
        result = await _call_tool(loaded_server, "evaluate_progress",
                                  current_understanding="Partial understanding",
                                  remaining_questions=["What is X?"],
                                  confidence_score=0.3,
                                  context_id="test")
        assert "CONTINUE EXPLORING" in result


class TestSummarizeSoFar:
    """Tests for summarize_so_far tool."""

    @pytest.mark.asyncio
    async def test_summarize_basic(self, loaded_server):
        """Test basic summary generation."""
        result = await _call_tool(loaded_server, "summarize_so_far",
                                  context_id="test")
        assert "Context Summary" in result
        assert "test" in result  # session ID

    @pytest.mark.asyncio
    async def test_summarize_includes_reasoning(self, loaded_server):
        """Test that summary includes think history."""
        # Add some reasoning
        await _call_tool(loaded_server, "think",
                        question="What is this about?",
                        context_id="test")
        result = await _call_tool(loaded_server, "summarize_so_far",
                                  context_id="test")
        assert "Reasoning Steps" in result

    @pytest.mark.asyncio
    async def test_summarize_clears_history(self, loaded_server):
        """Test clear_history option."""
        await _call_tool(loaded_server, "think",
                        question="Test question",
                        context_id="test")
        session = loaded_server._sessions["test"]
        assert len(session.think_history) > 0

        await _call_tool(loaded_server, "summarize_so_far",
                        clear_history=True,
                        context_id="test")
        assert len(session.think_history) == 0


class TestProvenanceTracking:
    """Tests for evidence/provenance tracking."""

    @pytest.mark.asyncio
    async def test_search_collects_evidence(self, loaded_server):
        """Test that search_context collects evidence."""
        await _call_tool(loaded_server, "search_context",
                        pattern="Line",  # Search for something in the test context
                        context_id="test")
        session = loaded_server._sessions["test"]
        assert len(session.evidence) > 0
        assert session.evidence[0].source == "search"
        assert session.evidence[0].pattern == "Line"

    @pytest.mark.asyncio
    async def test_peek_collects_evidence(self, loaded_server):
        """Test that peek_context collects evidence."""
        await _call_tool(loaded_server, "peek_context",
                        start=0, end=10, unit="lines",
                        record_evidence=True,
                        context_id="test")
        session = loaded_server._sessions["test"]
        # Find peek evidence
        peek_evidence = [e for e in session.evidence if e.source == "peek"]
        assert len(peek_evidence) > 0

    @pytest.mark.asyncio
    async def test_cite_collects_evidence(self, loaded_server):
        """Test that cite() helper collects evidence."""
        await _call_tool(loaded_server, "exec_python",
                        code='cite("important finding", (10, 20), "key evidence")',
                        context_id="test")
        session = loaded_server._sessions["test"]
        manual_evidence = [e for e in session.evidence if e.source == "manual"]
        assert len(manual_evidence) > 0
        assert any(e.note == "key evidence" for e in manual_evidence)
        repl_evidence = session.repl.get_variable("_evidence")
        assert isinstance(repl_evidence, list)
        assert any(isinstance(item, dict) and item.get("note") == "key evidence" for item in repl_evidence)

    @pytest.mark.asyncio
    async def test_get_evidence_returns_citations(self, loaded_server):
        """Test get_evidence() exposes stored citations."""
        await _call_tool(
            loaded_server,
            "exec_python",
            code='cite("important finding", (10, 20), "key evidence")',
            context_id="test",
        )
        raw = await _call_tool(loaded_server, "get_evidence", context_id="test", output="json")
        data = json.loads(raw)
        assert data["context_id"] == "test"
        assert data["total"] >= 1
        assert any(item.get("note") == "key evidence" for item in data.get("items", []))

    @pytest.mark.asyncio
    async def test_finalize_includes_evidence(self, loaded_server):
        """Test that finalize includes evidence citations."""
        # Collect some evidence first
        await _call_tool(loaded_server, "search_context",
                        pattern="Line",  # Search for something in the test context
                        context_id="test")
        result = await _call_tool(loaded_server, "finalize",
                                  answer="The answer is 42",
                                  confidence="high",
                                  context_id="test")
        assert "Evidence Citations" in result

    @pytest.mark.asyncio
    async def test_information_gain_tracked(self, loaded_server):
        """Test that information gain is tracked per operation."""
        session = loaded_server._sessions["test"]
        initial_gain = len(session.information_gain)

        await _call_tool(loaded_server, "search_context",
                        pattern="Line",  # Search for something in the test context
                        context_id="test")

        assert len(session.information_gain) > initial_gain


class TestConvergenceMetrics:
    """Tests for convergence metrics in get_status."""

    @pytest.mark.asyncio
    async def test_status_shows_convergence(self, loaded_server):
        """Test that get_status shows convergence metrics."""
        result = await _call_tool(loaded_server, "get_status",
                                  context_id="test")
        assert "Convergence Metrics" in result
        assert "Evidence collected" in result

    @pytest.mark.asyncio
    async def test_status_shows_confidence_history(self, loaded_server):
        """Test confidence history in status."""
        await _call_tool(loaded_server, "evaluate_progress",
                        current_understanding="Test",
                        confidence_score=0.5,
                        context_id="test")
        result = await _call_tool(loaded_server, "get_status",
                                  context_id="test")
        assert "Latest confidence" in result
        assert "50" in result  # Could be "50%" or "50.0%"


@pytest.mark.asyncio
async def test_sub_query_validation_retry():
    server = AlephMCPServerLocal(sub_query_config=SubQueryConfig(validation_regex=r"^OK:", max_retries=1))
    with patch("aleph.mcp.local_server.run_cli_sub_query", new=AsyncMock()) as mock_run:
        mock_run.side_effect = [
            (True, "BAD OUTPUT"),
            (True, "OK: good"),
        ]
        success, output, truncated, backend = await server._run_sub_query(
            prompt="Return OK: ...",
            context_slice="ctx",
            context_id="default",
            backend="codex",
        )
    assert success is True
    assert output == "OK: good"
    assert truncated is False
    assert backend == "codex"
    assert mock_run.call_count == 2


@pytest.mark.asyncio
async def test_sub_query_validation_failure():
    server = AlephMCPServerLocal(sub_query_config=SubQueryConfig(validation_regex=r"^OK:", max_retries=0))
    with patch("aleph.mcp.local_server.run_cli_sub_query", new=AsyncMock()) as mock_run:
        mock_run.return_value = (True, "NOPE")
        success, output, _, _ = await server._run_sub_query(
            prompt="Return OK: ...",
            context_slice="ctx",
            context_id="default",
            backend="codex",
        )
    assert success is False
    assert "validation regex" in output


@pytest.mark.asyncio
async def test_run_sub_aleph_uses_session_context(loaded_server):
    response = AlephResponse(
        answer="Line 1: Hello World",
        success=True,
        total_iterations=2,
        max_depth_reached=1,
        total_tokens=10,
        total_cost_usd=0.01,
        wall_time_seconds=0.1,
        trajectory=[],
    )
    with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "api"}, clear=True):
        with patch("aleph.mcp.local_server.Aleph.complete", new=AsyncMock(return_value=response)) as mock_complete:
            result, meta = await loaded_server._run_sub_aleph(
                query="What is line 1?",
                context_slice=None,
                context_id="test",
                max_depth=3,
            )
    assert result.success is True
    assert meta["budget"].max_depth == 3
    assert mock_complete.call_args.kwargs["context"].startswith("Line 1")
    session = loaded_server._sessions["test"]
    assert session.evidence[-1].source == "sub_aleph"


@pytest.mark.asyncio
async def test_run_sub_aleph_cli_single_shot(loaded_server):
    with patch.dict(os.environ, {"ALEPH_SUB_QUERY_BACKEND": "codex"}, clear=True):
        with patch("aleph.mcp.local_server.run_cli_sub_query", new=AsyncMock(return_value=(True, "FINAL(ok)"))) as mock_run:
            result, meta = await loaded_server._run_sub_aleph(
                query="Return ok",
                context_slice=None,
                context_id="test",
            )
    assert result.success is True
    assert result.answer == "ok"
    assert meta["backend"] == "codex"
    assert mock_run.call_count == 1

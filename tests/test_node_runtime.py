from __future__ import annotations

import shutil

import pytest

from aleph.repl.node_runtime import NodeREPLEnvironment


NODE_AVAILABLE = shutil.which("node") is not None


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required for JS/TS REPL tests")
class TestNodeRuntime:
    def test_exec_javascript_expression(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="hello", config=sandbox_config)
        try:
            result = repl.execute("1 + 1")
            assert result.error is None
            assert result.return_value == 2
        finally:
            repl.close()

    def test_exec_typescript_expression(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="hello", config=sandbox_config)
        try:
            result = repl.execute("const answer: number = 40; answer + 2", language="typescript")
            assert result.error is None
            assert result.return_value == 42
        finally:
            repl.close()

    def test_context_helpers_and_variable_lookup(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="Line 1: Hello World\nLine 2: Goodbye", config=sandbox_config)
        try:
            repl.set_variable("line_number_base", 1)
            result = repl.execute("const hits = search('Hello'); const answer = hits[0].match; answer")
            assert result.error is None
            assert result.return_value == "Line 1: Hello World"
            assert "answer" in result.variables_updated
            assert repl.get_variable("answer") == "Line 1: Hello World"
        finally:
            repl.close()

    def test_ctx_mutation_persists(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="before", config=sandbox_config)
        try:
            repl.execute("ctx_set('after')")
            assert repl.get_variable("ctx") == "after"
            repl.execute("ctx_append(' plus')")
            assert repl.get_variable("ctx") == "after plus"
        finally:
            repl.close()

    def test_top_level_await_callback_persists_variables(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="alpha\nbeta", config=sandbox_config)
        repl.register_callback(
            "sub_query",
            lambda prompt, context_slice=None: f"{prompt}|{context_slice}",
        )
        try:
            result = repl.execute(
                "const answer = await sub_query('Summarize', lines(0, 1)); answer",
            )
            assert result.error is None
            assert result.return_value == "Summarize|alpha"
            assert repl.get_variable("answer") == "Summarize|alpha"
        finally:
            repl.close()

    def test_expanded_text_helpers(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(
            context="TODO: investigate\nemail me at dev@example.com\n\nline 3",
            config=sandbox_config,
        )
        try:
            result = repl.execute(
                "({ emails: extract_emails(), todos: extract_todos(), numbered: number_lines(), paragraphs: paragraph_count() })",
            )
            assert result.error is None
            payload = result.return_value
            assert isinstance(payload, dict)
            assert payload["emails"][0]["value"] == "dev@example.com"
            assert payload["todos"][0]["value"] == "TODO: investigate"
            assert "1: TODO: investigate" in payload["numbered"]
            assert payload["paragraphs"] == 2
        finally:
            repl.close()

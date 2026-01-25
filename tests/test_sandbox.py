"""Tests for sandbox security and execution."""

from __future__ import annotations

import asyncio

import pytest

from aleph.repl.sandbox import (
    REPLEnvironment,
    SandboxConfig,
    SecurityError,
    _validate_ast,
    DEFAULT_ALLOWED_IMPORTS,
)


class TestForbiddenImports:
    """Tests that dangerous imports are blocked."""

    def test_forbidden_os(self) -> None:
        with pytest.raises(SecurityError, match="Import of module 'os'"):
            _validate_ast("import os", set(DEFAULT_ALLOWED_IMPORTS))

    def test_forbidden_subprocess(self) -> None:
        with pytest.raises(SecurityError, match="Import of module 'subprocess'"):
            _validate_ast("import subprocess", set(DEFAULT_ALLOWED_IMPORTS))

    def test_forbidden_socket(self) -> None:
        with pytest.raises(SecurityError, match="Import of module 'socket'"):
            _validate_ast("import socket", set(DEFAULT_ALLOWED_IMPORTS))

    def test_forbidden_sys(self) -> None:
        with pytest.raises(SecurityError, match="Import of module 'sys'"):
            _validate_ast("import sys", set(DEFAULT_ALLOWED_IMPORTS))

    def test_forbidden_from_import(self) -> None:
        with pytest.raises(SecurityError, match="Import of module 'socket'"):
            _validate_ast("from socket import socket", set(DEFAULT_ALLOWED_IMPORTS))

    def test_forbidden_importlib(self) -> None:
        with pytest.raises(SecurityError, match="Import of module 'importlib'"):
            _validate_ast("import importlib", set(DEFAULT_ALLOWED_IMPORTS))

    def test_forbidden_pathlib(self) -> None:
        with pytest.raises(SecurityError, match="Import of module 'pathlib'"):
            _validate_ast("import pathlib", set(DEFAULT_ALLOWED_IMPORTS))

    def test_allowed_json(self) -> None:
        # Should not raise
        _validate_ast("import json", set(DEFAULT_ALLOWED_IMPORTS))

    def test_allowed_re(self) -> None:
        # Should not raise
        _validate_ast("import re", set(DEFAULT_ALLOWED_IMPORTS))

    def test_allowed_collections(self) -> None:
        # Should not raise
        _validate_ast("from collections import Counter", set(DEFAULT_ALLOWED_IMPORTS))

    def test_relative_import_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="Relative imports are not allowed"):
            _validate_ast("from . import x", set(DEFAULT_ALLOWED_IMPORTS))


class TestForbiddenNames:
    """Tests that dangerous builtin names are blocked."""

    def test_eval_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'eval'"):
            _validate_ast("eval('1+1')", set())

    def test_exec_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'exec'"):
            _validate_ast("exec('x=1')", set())

    def test_compile_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'compile'"):
            _validate_ast("compile('x=1', '', 'exec')", set())

    def test_open_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'open'"):
            _validate_ast("open('/etc/passwd')", set())

    def test_import_builtin_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'__import__'"):
            _validate_ast("__import__('os')", set())

    def test_getattr_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'getattr'"):
            _validate_ast("getattr(x, 'y')", set())

    def test_setattr_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'setattr'"):
            _validate_ast("setattr(x, 'y', 1)", set())

    def test_globals_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'globals'"):
            _validate_ast("globals()", set())

    def test_locals_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'locals'"):
            _validate_ast("locals()", set())

    def test_builtins_name_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="'__builtins__'"):
            _validate_ast("__builtins__", set())

    def test_except_baseexception_forbidden(self) -> None:
        with pytest.raises(SecurityError, match="Catching BaseException-derived"):
            _validate_ast(
                "try:\n    1 / 0\nexcept BaseException:\n    pass",
                set(),
            )


class TestDunderAccess:
    """Tests that dunder attribute access is blocked."""

    def test_dunder_class(self) -> None:
        with pytest.raises(SecurityError, match="__class__"):
            _validate_ast("x.__class__", set())

    def test_dunder_bases(self) -> None:
        with pytest.raises(SecurityError, match="__bases__"):
            _validate_ast("str.__bases__", set())

    def test_dunder_subclasses(self) -> None:
        with pytest.raises(SecurityError, match="__subclasses__"):
            _validate_ast("str.__subclasses__()", set())

    def test_dunder_globals(self) -> None:
        with pytest.raises(SecurityError, match="__globals__"):
            _validate_ast("f.__globals__", set())

    def test_dunder_code(self) -> None:
        with pytest.raises(SecurityError, match="__code__"):
            _validate_ast("f.__code__", set())

    def test_dunder_builtins(self) -> None:
        with pytest.raises(SecurityError, match="__builtins__"):
            _validate_ast("x.__builtins__", set())


class TestSandboxExecution:
    """Tests for actual code execution in the sandbox."""

    def test_simple_execution(self, repl: REPLEnvironment) -> None:
        result = repl.execute("x = len(ctx)")
        assert result.error is None
        assert repl.get_variable("x") == len("test context for unit tests")

    def test_print_output(self, repl: REPLEnvironment) -> None:
        result = repl.execute("print('hello')")
        assert "hello" in result.stdout
        assert result.error is None

    def test_return_value(self, repl: REPLEnvironment) -> None:
        result = repl.execute("1 + 1")
        assert result.return_value == 2
        assert result.error is None

    def test_multi_statement(self, repl: REPLEnvironment) -> None:
        result = repl.execute("a = 1\nb = 2\na + b")
        assert result.return_value == 3
        assert repl.get_variable("a") == 1
        assert repl.get_variable("b") == 2

    def test_security_error_returns_error(self, repl: REPLEnvironment) -> None:
        result = repl.execute("import os")
        assert result.error is not None
        assert "os" in result.error

    def test_runtime_error_captured(self, repl: REPLEnvironment) -> None:
        result = repl.execute("1 / 0")
        assert result.error is not None
        assert "ZeroDivision" in result.error or "division by zero" in result.error

    def test_variables_updated_tracking(self, repl: REPLEnvironment) -> None:
        result = repl.execute("new_var = 42")
        assert "new_var" in result.variables_updated

    def test_context_variable_accessible(self, repl: REPLEnvironment) -> None:
        result = repl.execute("len(ctx)")
        assert result.return_value == len("test context for unit tests")

    def test_helper_peek_available(self, repl: REPLEnvironment) -> None:
        result = repl.execute("peek(0, 4)")
        assert result.return_value == "test"

    def test_helper_search_available(self, repl_multiline: REPLEnvironment) -> None:
        result = repl_multiline.execute("search('line2')")
        assert result.return_value is not None
        assert isinstance(result.return_value, list)
        assert len(result.return_value) > 0

    def test_helper_import_introspection(self, repl: REPLEnvironment) -> None:
        result = repl.execute("is_import_allowed('json')")
        assert result.return_value is True

    def test_sub_query_batch_helpers(self, repl: REPLEnvironment) -> None:
        def fake_sub_query(prompt: str, context_slice: str | None = None) -> str:
            return f"{prompt}|{context_slice}"

        repl.inject_sub_query(fake_sub_query)
        result = repl.execute("sub_query_batch('Do', ['a', 'b'])")
        assert result.return_value == ["Do|a", "Do|b"]

        result = repl.execute("sub_query_map(['P1', 'P2'], ['x', 'y'])")
        assert result.return_value == ["P1|x", "P2|y"]

    def test_sub_query_strict(self, repl: REPLEnvironment) -> None:
        responses = iter(["BAD", "OK: good"])

        def fake_sub_query(prompt: str, context_slice: str | None = None) -> str:
            return next(responses)

        repl.inject_sub_query(fake_sub_query)
        result = repl.execute("sub_query_strict('ignored', validate_regex=r'^OK:', max_retries=1)")
        assert result.return_value == "OK: good"

    def test_code_execution_disabled(self) -> None:
        config = SandboxConfig(enable_code_execution=False)
        repl = REPLEnvironment(context="test", config=config)
        result = repl.execute("x = 1")
        assert result.error is not None
        assert "disabled" in result.error.lower()

    def test_cite_invalid_line_range(self, repl: REPLEnvironment) -> None:
        result = repl.execute("cite('bad range', (5, 3))")
        assert result.error is not None
        assert "line_range" in result.error

    def test_output_truncation(self) -> None:
        config = SandboxConfig(max_output_chars=50)
        repl = REPLEnvironment(context="test", config=config)
        result = repl.execute("print('x' * 1000)")
        assert result.truncated is True
        assert "TRUNCATED" in result.stdout

    def test_timeout_enforced_execute_async(self) -> None:
        config = SandboxConfig(timeout_seconds=0.2)
        repl = REPLEnvironment(context="test", config=config)
        result = asyncio.run(
            asyncio.wait_for(
                repl.execute_async("while True:\n    pass"),
                timeout=2.0,
            )
        )
        assert result.error is not None
        assert "timeout" in result.error.lower()

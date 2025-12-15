"""Sandboxed Python execution environment.

Aleph stores the full context in a REPL namespace (default variable: `ctx`). The
root LLM can write Python code to inspect and process the context via helper
functions.

Security note
-------------
This sandbox is **best-effort**. It blocks obvious foot-guns (file I/O, network,
unsafe builtins, arbitrary imports), but it is not a formally hardened sandbox.
Do not expose Aleph code-execution mode to untrusted users without stronger
isolation (e.g., process sandboxing, containers, SELinux, gVisor, etc.).
"""

from __future__ import annotations

import ast
import builtins
import asyncio
import ctypes
import inspect
import signal
import sys
import threading
import time
from collections.abc import Coroutine, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from io import StringIO
from types import CodeType
from typing import Any, Awaitable, Callable, cast

from ..types import ContextType, ExecutionResult, SubAlephFn, SubQueryFn
from .helpers import chunk as _chunk
from .helpers import Citation
from .helpers import cite as _cite
from .helpers import lines as _lines
from .helpers import peek as _peek
from .helpers import search as _search


DEFAULT_ALLOWED_IMPORTS: list[str] = [
    "re",
    "json",
    "csv",
    "math",
    "statistics",
    "collections",
    "itertools",
    "functools",
    "datetime",
    "textwrap",
    "difflib",
]


FORBIDDEN_NAMES: set[str] = {
    # Dynamic code execution / introspection
    "eval",
    "exec",
    "compile",
    "__import__",
    "__builtins__",
    "open",
    "input",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    # Potentially dangerous builtins
    "memoryview",
    # process control
    "exit",
    "quit",
}


class SecurityError(RuntimeError):
    """Raised when code violates the sandbox policy."""


class ExecutionTimeout(BaseException):
    """Raised when code execution exceeds the time limit."""


@dataclass(slots=True)
class SandboxConfig:
    """Configuration for the sandbox environment."""

    allowed_imports: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_IMPORTS))
    max_output_chars: int = 10_000
    timeout_seconds: float = 30.0
    enable_code_execution: bool = True


def _safe_import_factory(allowed: set[str]) -> Callable[..., object]:
    """Return a __import__ implementation that only allows certain modules."""

    real_import = builtins.__import__

    def _safe_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        # Only check the top-level module (e.g., "json" for "json.tool").
        top = name.split(".", 1)[0]
        if top not in allowed:
            raise SecurityError(f"Import of module '{top}' is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    return _safe_import


def _safe_builtins(allowed_imports: list[str]) -> dict[str, object]:
    """Construct a restricted __builtins__ dict."""

    allowed_imports_set = set(allowed_imports)

    safe: dict[str, object] = {
        # basic types / constructors
        "None": None,
        "True": True,
        "False": False,
        "bool": bool,
        "int": int,
        "float": float,
        "str": str,
        "dict": dict,
        "list": list,
        "set": set,
        "tuple": tuple,
        "type": type,
        # iteration / misc
        "len": len,
        "range": range,
        "enumerate": enumerate,
        "zip": zip,
        "min": min,
        "max": max,
        "sum": sum,
        "sorted": sorted,
        "reversed": reversed,
        "any": any,
        "all": all,
        "abs": abs,
        "round": round,
        "print": print,
        "isinstance": isinstance,
        # exceptions
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "RuntimeError": RuntimeError,
        "KeyError": KeyError,
        "IndexError": IndexError,
        "ZeroDivisionError": ZeroDivisionError,
        "NameError": NameError,
        "AttributeError": AttributeError,
        # controlled imports
        "__import__": _safe_import_factory(allowed_imports_set),
    }

    return safe


def _execute_with_timeout(
    exec_fn: Callable[[], object],
    timeout_seconds: float,
) -> object:
    """Execute a function with a timeout.

    On Unix main thread, uses SIGALRM for reliable interruption of CPU-bound code.
    Otherwise, uses threading-based timeout (cannot interrupt CPU-bound loops).

    Args:
        exec_fn: Zero-argument callable to execute.
        timeout_seconds: Maximum execution time in seconds.

    Returns:
        The return value of exec_fn.

    Raises:
        ExecutionTimeout: If execution exceeds the timeout.
    """
    if timeout_seconds <= 0:
        return exec_fn()

    # Check if we can use signal-based timeout (Unix main thread only)
    can_use_signal = (
        sys.platform != "win32"
        and hasattr(signal, "SIGALRM")
        and threading.current_thread() is threading.main_thread()
    )

    if can_use_signal:
        def _timeout_handler(signum: int, frame: object) -> None:
            raise ExecutionTimeout(
                f"Code execution exceeded {timeout_seconds:.1f}s timeout"
            )

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        # Use setitimer for sub-second precision
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        try:
            return exec_fn()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)

    def _raise_async(thread_id: int, exc_type: type[BaseException]) -> None:
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread_id),
            ctypes.py_object(exc_type),
        )
        if res == 0:
            raise RuntimeError("Failed to interrupt execution (invalid thread id)")
        if res != 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)
            raise RuntimeError("Failed to interrupt execution (async exception injection failed)")

    # Fallback: run in a separate thread and enforce timeout with join().
    # This is best-effort; it can interrupt typical Python CPU-bound loops.
    result_box: dict[str, object] = {}
    error_box: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_box["value"] = exec_fn()
        except BaseException as e:  # propagate to caller
            error_box["error"] = e

    worker = threading.Thread(target=_runner, daemon=True)
    start = time.monotonic()
    worker.start()
    worker.join(timeout_seconds)

    if worker.is_alive():
        if worker.ident is not None:
            try:
                _raise_async(worker.ident, ExecutionTimeout)
            except Exception:
                pass
        worker.join(0.1)
        elapsed = time.monotonic() - start
        raise ExecutionTimeout(
            f"Code execution exceeded {timeout_seconds:.1f}s timeout (took {elapsed:.1f}s)"
        )

    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def _compile_with_last_expr(source: str) -> tuple[CodeType, CodeType | None]:
    """Compile source for exec and optionally a last-expression eval.

    If the last statement is an expression, we compile it separately so we can
    return its value.
    """

    tree = ast.parse(source, mode="exec")
    if tree.body:
        last_stmt = tree.body[-1]
        if isinstance(last_stmt, ast.Expr):
            tree.body = tree.body[:-1]
            expr = ast.Expression(body=last_stmt.value)
            exec_code = compile(tree, filename="<aleph_repl>", mode="exec")
            eval_code = compile(expr, filename="<aleph_repl_expr>", mode="eval")
            return exec_code, eval_code

    exec_code = compile(tree, filename="<aleph_repl>", mode="exec")
    return exec_code, None


def _validate_ast(source: str, allowed_imports: set[str]) -> None:
    """Static checks for obviously unsafe constructs."""

    tree = ast.parse(source, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                raise SecurityError("Bare except handlers are not allowed")

            forbidden_excepts = {"BaseException", "SystemExit", "KeyboardInterrupt", "GeneratorExit"}

            def _contains_forbidden_except(exc: ast.AST) -> bool:
                if isinstance(exc, ast.Name):
                    return exc.id in forbidden_excepts
                if isinstance(exc, ast.Tuple):
                    return any(_contains_forbidden_except(elt) for elt in exc.elts)
                return False

            if _contains_forbidden_except(node.type):
                raise SecurityError("Catching BaseException-derived exceptions is not allowed")

        if isinstance(node, ast.ClassDef):
            raise SecurityError("Class definitions are not allowed")

        # Forbid dunder attribute access (__class__, __subclasses__, etc.)
        if isinstance(node, ast.Attribute):
            if isinstance(node.attr, str) and node.attr.startswith("__"):
                raise SecurityError(f"Access to dunder attribute '{node.attr}' is not allowed")

        # Forbid calling forbidden builtins by name
        if isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                raise SecurityError(f"Use of name '{node.id}' is not allowed")

        # Restrict import statements to allowed modules
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top not in allowed_imports:
                    raise SecurityError(f"Import of module '{top}' is not allowed")

        if isinstance(node, ast.ImportFrom):
            if getattr(node, "level", 0):
                raise SecurityError("Relative imports are not allowed")
            module = node.module or ""
            top = module.split(".", 1)[0] if module else ""
            if top and top not in allowed_imports:
                raise SecurityError(f"Import of module '{top}' is not allowed")
            # Block star imports
            for alias in node.names:
                if alias.name == "*":
                    raise SecurityError("Star imports ('from x import *') are not allowed")

        # Block type() with 3 args (dynamic class creation)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "type":
                if len(node.args) == 3:
                    raise SecurityError(
                        "Dynamic class creation via type() with 3 arguments is not allowed"
                    )

        # Block subscript access to dunder names (e.g., globals()['__builtins__'])
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant):
                if isinstance(node.slice.value, str) and node.slice.value.startswith("__"):
                    raise SecurityError(
                        f"Subscript access to '{node.slice.value}' is not allowed"
                    )


class REPLEnvironment:
    """Stateful sandboxed REPL environment."""

    def __init__(
        self,
        context: ContextType,
        context_var_name: str = "ctx",
        config: SandboxConfig | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self.context_var_name = context_var_name
        self._loop = loop

        # Base namespace (globals/locals for exec)
        self._namespace: dict[str, object] = {
            context_var_name: context,
            "__builtins__": _safe_builtins(self.config.allowed_imports),
        }

        # Citation storage for provenance tracking
        self._citations: list[Citation] = []
        self._evidence: list[Citation] = []

        # Helper functions (wrappers around repl.helpers)
        def _cite_and_store(
            snippet: str,
            line_range: tuple[int, int] | None = None,
            note: str | None = None,
        ) -> Citation:
            """Cite evidence and store it for provenance tracking."""
            citation = _cite(snippet, line_range, note)
            self._citations.append(citation)
            self._evidence.append(citation)
            return citation

        self._namespace.update(
            {
                "peek": lambda start=0, end=None: _peek(self._namespace[context_var_name], start, end),
                "lines": lambda start=0, end=None: _lines(self._namespace[context_var_name], start, end),
                "search": lambda pattern, context_lines=2, flags=0, max_results=20: _search(
                    self._namespace[context_var_name],
                    pattern,
                    context_lines=context_lines,
                    flags=flags,
                    max_results=max_results,
                ),
                "chunk": lambda chunk_size, overlap=0: _chunk(
                    self._namespace[context_var_name], chunk_size=chunk_size, overlap=overlap
                ),
                "cite": _cite_and_store,
                "_evidence": self._evidence,
            }
        )

        self._sub_query_fn: SubQueryFn | None = None
        self._sub_aleph_fn: SubAlephFn | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Set/replace the event loop used to bridge async calls (sub_query)."""

        self._loop = loop

    def inject_sub_query(self, fn: SubQueryFn) -> None:
        """Inject sub_query(prompt, context_slice=None) into the REPL namespace.

        The injected function is **synchronous** from the REPL's perspective.
        Internally it may schedule an async coroutine on the Aleph event loop.
        """

        self._sub_query_fn = fn
        self._namespace["sub_query"] = self._sync_bridge(fn)

    def inject_sub_aleph(self, fn: SubAlephFn) -> None:
        """Inject sub_aleph(query, context=None) into the REPL namespace."""

        self._sub_aleph_fn = fn
        self._namespace["sub_aleph"] = self._sync_bridge(fn)

    def _sync_bridge(self, fn: Callable[..., object | Awaitable[object]]) -> Callable[..., object]:
        """Wrap an async (or sync) function so it can be called synchronously."""

        def _wrapped(*args: object, **kwargs: object) -> object:
            result = fn(*args, **kwargs)
            if not inspect.isawaitable(result):
                return result

            if self._loop is None:
                raise RuntimeError("No event loop available for async bridge")
            # Must be called from a different thread than the event loop.
            if threading.current_thread() is threading.main_thread() and self._loop.is_running():
                # If called on main thread while the loop runs, blocking would deadlock.
                raise RuntimeError(
                    "sub_query/sub_aleph called from the event loop thread. "
                    "Aleph runs REPL code in a worker thread; if you are calling execute() "
                    "directly, use execute_async() or run it in a thread."
                )

            coro = cast(Coroutine[Any, Any, object], result)
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return fut.result()

        return _wrapped

    def get_variable(self, name: str) -> object | None:
        return self._namespace.get(name)

    def set_variable(self, name: str, value: object) -> None:
        self._namespace[name] = value

    def execute(self, code: str) -> ExecutionResult:
        """Execute code in the sandbox.

        This method is synchronous. If you want to call it from async code while
        still allowing sub_query/sub_aleph, run it in a worker thread (Aleph does
        this automatically).
        """

        if not self.config.enable_code_execution:
            return ExecutionResult(
                stdout="",
                stderr="",
                return_value=None,
                variables_updated=[],
                truncated=False,
                execution_time_ms=0.0,
                error="Code execution disabled",
            )

        start = time.time()
        stdout_io = StringIO()
        stderr_io = StringIO()

        allowed_imports = set(self.config.allowed_imports)

        try:
            _validate_ast(code, allowed_imports)
            exec_code, eval_code = _compile_with_last_expr(code)

            # Track variable bindings (rebinding detection)
            before_ids: dict[str, int] = {
                k: id(v) for k, v in self._namespace.items() if k not in {"__builtins__"}
            }

            ret: object | None = None

            def _do_exec() -> object:
                """Inner function to execute code (wrapped with timeout)."""
                nonlocal ret
                with redirect_stdout(stdout_io), redirect_stderr(stderr_io):
                    exec(exec_code, self._namespace, self._namespace)
                    if eval_code is not None:
                        ret = eval(eval_code, self._namespace, self._namespace)
                return ret

            _execute_with_timeout(_do_exec, self.config.timeout_seconds)

            # Determine updated variables (new or rebound)
            updated: list[str] = []
            for k, v in self._namespace.items():
                if k == "__builtins__":
                    continue
                if k not in before_ids:
                    updated.append(k)
                else:
                    if id(v) != before_ids[k]:
                        updated.append(k)

            stdout = stdout_io.getvalue()
            stderr = stderr_io.getvalue()
            truncated = False

            if len(stdout) > self.config.max_output_chars:
                stdout = stdout[: self.config.max_output_chars] + "\n... [OUTPUT TRUNCATED]"
                truncated = True
            if len(stderr) > self.config.max_output_chars:
                stderr = stderr[: self.config.max_output_chars] + "\n... [OUTPUT TRUNCATED]"
                truncated = True

            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                return_value=ret,
                variables_updated=sorted(updated),
                truncated=truncated,
                execution_time_ms=(time.time() - start) * 1000.0,
                error=None,
            )

        except ExecutionTimeout as e:
            return ExecutionResult(
                stdout=stdout_io.getvalue(),
                stderr="",
                return_value=None,
                variables_updated=[],
                truncated=False,
                execution_time_ms=(time.time() - start) * 1000.0,
                error=str(e),
            )
        except Exception as e:
            return ExecutionResult(
                stdout=stdout_io.getvalue(),
                stderr=stderr_io.getvalue() or str(e),
                return_value=None,
                variables_updated=[],
                truncated=False,
                execution_time_ms=(time.time() - start) * 1000.0,
                error=str(e),
            )

    async def execute_async(self, code: str) -> ExecutionResult:
        """Async helper that runs execute() in a worker thread."""

        return await asyncio.to_thread(self.execute, code)

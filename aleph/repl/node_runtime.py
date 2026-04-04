"""Persistent Node.js runtime for JavaScript and TypeScript execution."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import inspect
import json
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import threading
import time
from typing import Any, Awaitable, Callable, Coroutine, Literal, cast

from ..types import ExecutionResult
from .sandbox import SandboxConfig


NodeLanguage = Literal["javascript", "typescript"]
NodeCallback = Callable[..., object | Awaitable[object]]
_REQUEST_TIMEOUT_PAD_SECONDS = 1.0


@dataclass(slots=True)
class _SerializedValue:
    kind: str
    value: Any = None


class NodeREPLEnvironment:
    """Stateful JavaScript/TypeScript runtime backed by Node's ``vm`` module."""

    def __init__(
        self,
        context: object,
        *,
        context_var_name: str = "ctx",
        config: SandboxConfig | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self.context_var_name = context_var_name
        self._loop = loop
        self._context_cache = self._coerce_context_to_text(context)
        self._line_number_base = 1
        self._lock = threading.Lock()
        self._responses: Queue[dict[str, Any]] = Queue()
        self._stderr_lines: deque[str] = deque(maxlen=40)
        self._request_id = 0
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._last_citations: list[dict[str, Any]] = []
        self._worker_needs_sync = True
        self._callbacks: dict[str, NodeCallback] = {}

    def __del__(self) -> None:  # pragma: no cover - destructor best effort only
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _coerce_context_to_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (dict, list, tuple)):
            try:
                return json.dumps(value, ensure_ascii=False, indent=2)
            except Exception:
                return str(value)
        return str(value)

    def set_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def register_callback(self, name: str, fn: NodeCallback | None) -> None:
        if fn is None:
            self._callbacks.pop(name, None)
            return
        self._callbacks[name] = fn

    def set_variable(self, name: str, value: object) -> None:
        if name == self.context_var_name:
            self.sync_context(value, self._line_number_base)
            return
        if name == "line_number_base":
            base = int(value) if isinstance(value, (int, str)) else 1
            self.sync_context(self._context_cache, base)
            return

        self._send_request(
            {
                "op": "set_variable",
                "name": name,
                "value": value,
            }
        )

    def get_variable(self, name: str) -> object | None:
        response = self._send_request({"op": "get_variable", "name": name})
        if not response.get("found", False):
            return None
        return self._deserialize_value(response.get("value"))

    def drain_citations(self) -> list[dict[str, Any]]:
        citations = list(self._last_citations)
        self._last_citations.clear()
        return citations

    def sync_context(self, context: object, line_number_base: int = 1) -> None:
        self._context_cache = self._coerce_context_to_text(context)
        self._line_number_base = line_number_base
        self._send_request(
            {
                "op": "sync_context",
                "context": self._context_cache,
                "line_number_base": line_number_base,
            }
        )

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.write(json.dumps({"op": "close"}) + "\n")
                process.stdin.flush()
        except Exception:
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    async def execute_async(
        self,
        code: str,
        *,
        language: NodeLanguage = "javascript",
    ) -> ExecutionResult:
        return await asyncio.to_thread(self.execute, code, language=language)

    def execute(
        self,
        code: str,
        *,
        language: NodeLanguage = "javascript",
    ) -> ExecutionResult:
        response = self._send_request(
            {
                "op": "exec",
                "code": code,
                "language": language,
                "timeout_ms": max(1, int(self.config.timeout_seconds * 1000)),
            },
            timeout_seconds=self.config.timeout_seconds + _REQUEST_TIMEOUT_PAD_SECONDS,
        )
        self._last_citations = list(response.get("citations", []))

        ctx_payload = response.get("context")
        if ctx_payload is not None:
            ctx_value = self._deserialize_value(ctx_payload)
            self._context_cache = self._coerce_context_to_text(ctx_value)

        if response.get("ok", False):
            return ExecutionResult(
                stdout=str(response.get("stdout", "")),
                stderr=str(response.get("stderr", "")),
                return_value=self._deserialize_value(response.get("return_value")),
                variables_updated=[str(v) for v in response.get("variables_updated", [])],
                truncated=False,
                execution_time_ms=float(response.get("execution_time_ms", 0.0)),
                error=None,
            )

        return ExecutionResult(
            stdout=str(response.get("stdout", "")),
            stderr=str(response.get("stderr", "")),
            return_value=None,
            variables_updated=[],
            truncated=False,
            execution_time_ms=float(response.get("execution_time_ms", 0.0)),
            error=str(response.get("error", "Node execution failed")),
        )

    def _deserialize_value(self, payload: object) -> object | None:
        if not isinstance(payload, dict):
            return payload
        value = _SerializedValue(
            kind=str(payload.get("kind", "json")),
            value=payload.get("value"),
        )
        if value.kind == "undefined":
            return None
        return value.value

    def _serialize_callback_value(self, value: object) -> object:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, Enum):
            return self._serialize_callback_value(value.value)
        if is_dataclass(value):
            return self._serialize_callback_value(asdict(value))
        if isinstance(value, dict):
            return {
                str(key): self._serialize_callback_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._serialize_callback_value(item) for item in value]
        if isinstance(value, set):
            return [self._serialize_callback_value(item) for item in value]
        return str(value)

    def _ensure_worker(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        node_bin = shutil.which("node")
        if not node_bin:
            raise RuntimeError(
                "Node.js is required for exec_javascript/exec_typescript but was not found on PATH."
            )

        worker_path = Path(__file__).with_name("node_worker.cjs")
        if not worker_path.exists():
            raise RuntimeError(f"Node worker script not found: {worker_path}")

        self._responses = Queue()
        self._stderr_lines.clear()
        self._worker_needs_sync = True
        self._process = subprocess.Popen(
            [node_bin, str(worker_path), self.context_var_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except Exception:
                self._responses.put(
                    {
                        "ok": False,
                        "error": f"Malformed JSON from Node worker: {line[:200]}",
                    }
                )
        self._responses.put({"ok": False, "error": "Node worker exited unexpectedly."})

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            stripped = line.strip()
            if stripped:
                self._stderr_lines.append(stripped)

    def _send_request(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_worker()
            if self._worker_needs_sync and payload.get("op") != "sync_context":
                self._send_request_locked(
                    {
                        "op": "sync_context",
                        "context": self._context_cache,
                        "line_number_base": self._line_number_base,
                    },
                    timeout_seconds=timeout_seconds,
                )
                self._worker_needs_sync = False
            elif payload.get("op") == "sync_context":
                self._worker_needs_sync = False

            return self._send_request_locked(payload, timeout_seconds=timeout_seconds)

    def _send_request_locked(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Node worker is not available")

        self._request_id += 1
        request_id = self._request_id
        outbound = dict(payload)
        outbound["id"] = request_id

        self._write_message_locked(outbound)

        wait_seconds = timeout_seconds or (self.config.timeout_seconds + _REQUEST_TIMEOUT_PAD_SECONDS)
        deadline = time.monotonic() + wait_seconds

        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                self.close()
                raise RuntimeError("Timed out waiting for Node worker response")
            try:
                response = self._responses.get(timeout=remaining)
            except Empty as exc:
                self.close()
                raise RuntimeError("Timed out waiting for Node worker response") from exc

            if response.get("op") == "callback_request":
                self._handle_callback_request_locked(response, timeout_seconds=remaining)
                continue

            if response.get("id") != request_id and response.get("id") is not None:
                self.close()
                raise RuntimeError("Node worker response stream fell out of sync")
            break

        if not response.get("ok", True) and payload.get("op") != "exec":
            err = str(response.get("error", "Node worker request failed"))
            stderr_tail = "\n".join(self._stderr_lines)
            if stderr_tail:
                err += f"\nNode stderr:\n{stderr_tail}"
            raise RuntimeError(err)
        return response

    def _handle_callback_request_locked(
        self,
        request: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> None:
        callback_id = request.get("callback_id")
        name = str(request.get("name", ""))
        args = request.get("args", [])
        kwargs = request.get("kwargs", {})

        try:
            if not isinstance(args, list):
                raise RuntimeError("callback args must be a list")
            if not isinstance(kwargs, dict):
                raise RuntimeError("callback kwargs must be an object")

            fn = self._callbacks.get(name)
            if fn is None:
                raise RuntimeError(f"Node callback '{name}' is not registered")

            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                if self._loop is None:
                    raise RuntimeError(
                        f"Node callback '{name}' requires an event loop but none is configured"
                    )
                coro = cast(Coroutine[Any, Any, object], result)
                if self._loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
                    result = fut.result(timeout=timeout_seconds)
                else:
                    result = self._loop.run_until_complete(coro)

            self._write_message_locked(
                {
                    "op": "callback_response",
                    "callback_id": callback_id,
                    "ok": True,
                    "value": self._serialize_callback_value(result),
                }
            )
        except Exception as exc:
            self._write_message_locked(
                {
                    "op": "callback_response",
                    "callback_id": callback_id,
                    "ok": False,
                    "error": str(exc),
                }
            )

    def _write_message_locked(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Node worker is not available")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except Exception as exc:
            self.close()
            raise RuntimeError(f"Failed to send request to Node worker: {exc}") from exc

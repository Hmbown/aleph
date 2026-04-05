"""REPL helper injection for the local MCP server."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..types import AlephResponse, ContextType
from .session import _Session, _coerce_context_to_text

if TYPE_CHECKING:
    from .local_server import AlephMCPServerLocal


__all__ = [
    "configure_session",
    "inject_repl_config_helpers",
    "inject_repl_sub_aleph",
    "inject_repl_sub_query",
]


def inject_repl_config_helpers(
    owner: "AlephMCPServerLocal",
    session: _Session,
) -> None:
    """Expose runtime configuration helpers inside the Python REPL."""

    def set_backend(backend: str) -> str:
        ok, message = owner._apply_sub_query_runtime_config(sub_query_backend=backend)
        if not ok:
            raise ValueError(message)
        snapshot = owner._get_sub_query_config_snapshot()
        return (
            "sub_query_backend set to "
            f"{snapshot['sub_query_backend']!r} "
            f"(resolved: {snapshot['sub_query_backend_resolved']!r})"
        )

    def get_config() -> dict[str, object]:
        return owner._get_sub_query_config_snapshot()

    session.repl.set_variable("set_backend", set_backend)
    session.repl.set_variable("get_config", get_config)


def inject_repl_sub_query(
    owner: "AlephMCPServerLocal",
    session: _Session,
    context_id: str,
) -> None:
    """Inject the recursive `sub_query` helper into the Python REPL."""

    async def sub_query(prompt: str, context_slice: str | None = None) -> str:
        success, output, _truncated, _backend = await owner._run_sub_query(
            prompt=prompt,
            context_slice=context_slice,
            context_id=context_id,
            backend="auto",
        )
        if not success:
            return f"[ERROR: sub_query failed: {output}]"
        return output

    session.repl.inject_sub_query(sub_query)


def inject_repl_sub_aleph(
    owner: "AlephMCPServerLocal",
    session: _Session,
    context_id: str,
) -> None:
    """Inject the recursive `sub_aleph` helper into the Python REPL."""

    async def sub_aleph(
        query: str,
        context: ContextType | None = None,
    ) -> AlephResponse:
        context_slice: str | None
        if context is None:
            context_slice = None
        elif isinstance(context, str):
            context_slice = context
        else:
            context_slice = _coerce_context_to_text(context)
        response, _meta = await owner._run_sub_aleph(
            query=query,
            context_slice=context_slice,
            context_id=context_id,
        )
        return response

    session.repl.inject_sub_aleph(sub_aleph)


def configure_session(
    owner: "AlephMCPServerLocal",
    session: _Session,
    context_id: str,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Configure a session REPL with loop state and recursive helpers."""

    if loop is not None:
        session.repl.set_loop(loop)
    inject_repl_sub_query(owner, session, context_id)
    inject_repl_sub_aleph(owner, session, context_id)
    inject_repl_config_helpers(owner, session)

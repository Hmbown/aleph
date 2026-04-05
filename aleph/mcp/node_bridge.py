"""Node REPL bridge for the local MCP server.

Manages the lifecycle of per-context NodeREPLEnvironment instances:
creation, context sync, callback registration, and teardown.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, cast

from ..repl.node_runtime import NodeREPLEnvironment
from ..repl.sandbox import SandboxConfig
from .session import _Session, _coerce_context_to_text

if TYPE_CHECKING:
    from ..types import ContentFormat, ContextMetadata


__all__ = [
    "close_node_repl",
    "configure_node_repl",
    "get_or_create_node_repl",
    "sync_session_from_node_repl",
]


def close_node_repl(
    node_repls: dict[str, NodeREPLEnvironment],
    context_id: str,
) -> None:
    """Remove and close the Node REPL for *context_id*, if any."""
    node_repl = node_repls.pop(context_id, None)
    if node_repl is not None:
        node_repl.close()


def configure_node_repl(
    node_repl: NodeREPLEnvironment,
    session: _Session,
) -> None:
    """Register Python→Node callback bridges on *node_repl*.

    Each callback delegates to the corresponding helper/variable already
    injected into the Python *session.repl* by the REPL injection layer.
    """

    def _get_helper(name: str) -> Callable[..., Any]:
        fn = session.repl.get_helper(name)
        if not callable(fn):
            raise RuntimeError(f"{name} is not available in this REPL session")
        return cast(Callable[..., Any], fn)

    def _get_callable(name: str) -> Callable[..., Any]:
        fn = session.repl.get_variable(name)
        if not callable(fn):
            raise RuntimeError(f"{name} is not available in this REPL session")
        return cast(Callable[..., Any], fn)

    node_repl.register_callback(
        "sub_query",
        lambda prompt, context_slice=None: _get_callable("sub_query")(
            prompt, context_slice
        ),
    )
    node_repl.register_callback(
        "sub_query_map",
        lambda prompts, context_slices=None, limit=None, parallel=True: _get_helper(
            "sub_query_map"
        )(
            prompts,
            context_slices=context_slices,
            limit=limit,
            parallel=parallel,
        ),
    )
    node_repl.register_callback(
        "sub_query_batch",
        lambda prompt, context_slices, limit=None: _get_helper("sub_query_batch")(
            prompt,
            context_slices,
            limit=limit,
        ),
    )
    node_repl.register_callback(
        "sub_query_strict",
        lambda prompt, context_slice=None, validate_regex=None, max_retries=0, retry_prompt=None: (
            _get_helper("sub_query_strict")(
                prompt,
                context_slice=context_slice,
                validate_regex=validate_regex,
                max_retries=max_retries,
                retry_prompt=retry_prompt,
            )
        ),
    )
    node_repl.register_callback(
        "sub_aleph",
        lambda query, context=None: _get_callable("sub_aleph")(query, context),
    )
    node_repl.register_callback(
        "set_backend", lambda backend: _get_callable("set_backend")(backend)
    )
    node_repl.register_callback("get_config", lambda: _get_callable("get_config")())


def get_or_create_node_repl(
    node_repls: dict[str, NodeREPLEnvironment],
    sessions: dict[str, _Session],
    context_id: str,
    sandbox_config: SandboxConfig,
) -> NodeREPLEnvironment:
    """Return the Node REPL for *context_id*, creating one if needed.

    The returned REPL has its context synced and callbacks configured.
    """
    if context_id not in sessions:
        raise KeyError(context_id)

    session = sessions[context_id]
    node_repl = node_repls.get(context_id)
    current_ctx = session.repl.get_variable("ctx")
    current_loop = asyncio.get_running_loop()

    if node_repl is None:
        node_repl = NodeREPLEnvironment(
            context=current_ctx,
            context_var_name="ctx",
            config=sandbox_config,
            loop=current_loop,
        )
        node_repls[context_id] = node_repl
    else:
        node_repl.set_loop(current_loop)

    node_repl.sync_context(current_ctx, session.line_number_base)
    configure_node_repl(node_repl, session)
    return node_repl


def sync_session_from_node_repl(
    node_repls: dict[str, NodeREPLEnvironment],
    sessions: dict[str, _Session],
    context_id: str,
    analyze_text_context: Callable[[str, "ContentFormat"], "ContextMetadata"],
) -> list[dict[str, Any]]:
    """Sync state from Node REPL back into the Python session.

    Returns any citations drained from the Node REPL.
    """
    node_repl = node_repls.get(context_id)
    if node_repl is None or context_id not in sessions:
        return []

    session = sessions[context_id]
    ctx_value = node_repl.get_variable("ctx")
    ctx_text = _coerce_context_to_text(ctx_value)
    session.repl.set_variable("ctx", ctx_text)
    session.meta = analyze_text_context(ctx_text, session.meta.format)
    return node_repl.drain_citations()

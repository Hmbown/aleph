"""Deprecated compatibility shim for legacy tool registration.

`aleph.mcp.tool_registry` previously hosted a parallel implementation of MCP tools.
The canonical implementation now lives in `aleph.mcp.local_server.AlephMCPServerLocal`.
"""

from __future__ import annotations

import warnings
from typing import Any

__all__ = ["register_tools"]

_DEPRECATION_MESSAGE = (
    "aleph.mcp.tool_registry.register_tools() has been removed. "
    "Use aleph.mcp.local_server.AlephMCPServerLocal instead."
)


def register_tools(server: Any) -> None:
    """Deprecated entrypoint kept only for import compatibility."""
    warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
    raise RuntimeError(_DEPRECATION_MESSAGE)

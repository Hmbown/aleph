"""Regression tests for the deprecated tool_registry shim."""

from __future__ import annotations

import warnings

import pytest

from aleph.mcp import tool_registry


def test_register_tools_raises_runtime_error() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="has been removed"):
            tool_registry.register_tools(object())

    assert any(issubclass(w.category, DeprecationWarning) for w in caught)

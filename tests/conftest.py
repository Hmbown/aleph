"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the package is importable when running tests from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aleph.repl.sandbox import REPLEnvironment, SandboxConfig  # noqa: E402


@pytest.fixture
def sandbox_config() -> SandboxConfig:
    """Default sandbox configuration for tests."""
    return SandboxConfig(timeout_seconds=5.0)


@pytest.fixture
def repl(sandbox_config: SandboxConfig) -> REPLEnvironment:
    """REPL environment with test context."""
    return REPLEnvironment(
        context="test context for unit tests",
        context_var_name="ctx",
        config=sandbox_config,
    )


@pytest.fixture
def repl_multiline(sandbox_config: SandboxConfig) -> REPLEnvironment:
    """REPL environment with multiline context."""
    context = "line0\nline1\nline2\nline3\nline4\nline5"
    return REPLEnvironment(
        context=context,
        context_var_name="ctx",
        config=sandbox_config,
    )

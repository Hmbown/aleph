"""Regression tests for context isolation and bounded REPL feedback."""

from __future__ import annotations

from aleph.core import Aleph
from aleph.prompts.system import DEFAULT_SYSTEM_PROMPT
from aleph.repl.sandbox import SandboxConfig
from aleph.types import ContentFormat, ContextMetadata, ExecutionResult


def test_default_prompt_omits_context_preview_placeholder() -> None:
    assert "{context_preview}" not in DEFAULT_SYSTEM_PROMPT
    assert "Preview (first 500 chars)" not in DEFAULT_SYSTEM_PROMPT


def test_build_initial_messages_redacts_context_preview_value() -> None:
    aleph = Aleph.__new__(Aleph)
    aleph.system_prompt = DEFAULT_SYSTEM_PROMPT + "\npreview={context_preview}"
    aleph.context_var_name = "ctx"

    meta = ContextMetadata(
        format=ContentFormat.TEXT,
        size_bytes=123,
        size_chars=123,
        size_lines=3,
        size_tokens_estimate=30,
        structure_hint="text",
        sample_preview="TOP_SECRET_CONTEXT",
    )

    messages = aleph._build_initial_messages("Question?", meta)
    system_message = messages[0]["content"]
    assert "TOP_SECRET_CONTEXT" not in system_message
    assert "[OMITTED FOR CONTEXT ISOLATION]" in system_message


def test_format_repl_result_truncates_large_return_value() -> None:
    aleph = Aleph.__new__(Aleph)
    aleph.sandbox_config = SandboxConfig(max_output_chars=120)
    aleph.output_feedback = "full"

    result = ExecutionResult(
        stdout="",
        stderr="",
        return_value="A" * 5000,
        variables_updated=[],
        truncated=False,
        execution_time_ms=1.0,
        error=None,
    )

    rendered = aleph._format_repl_result(result)
    assert "TRUNCATED" in rendered
    assert "A" * 1000 not in rendered

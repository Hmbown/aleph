"""Run the Aleph RLM LangGraph agent against repository files with LangSmith tracing.

This script is designed for practical repo-improvement runs:
- Loads selected repository files into Aleph context via `load_context`
- Asks the recursive LangGraph agent for high-impact improvements
- Optionally wraps execution in LangSmith tracing context

Typical run:
    export LANGSMITH_TRACING=true
    export LANGSMITH_API_KEY=...
    export OPENAI_API_KEY=...
    python3 examples/langgraph_rlm_repo_improver.py \
      --files README.md pyproject.toml aleph/integrations/langgraph_rlm.py
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from aleph.integrations.langgraph_rlm import (
    AlephRLMConfig,
    build_rlm_default_graph,
    collect_tool_trace,
    invoke_rlm,
)

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_DEFAULT_FILES: tuple[str, ...] = (
    "README.md",
    "pyproject.toml",
    "aleph/integrations/langgraph_rlm.py",
)
_DEFAULT_QUERY = (
    "Review this repository snapshot and propose the top 5 highest-impact improvements. "
    "For each improvement include: why it matters, affected files, and a concrete implementation sketch."
)


def _provider_requirements(model: str) -> tuple[str | None, str | None, str | None]:
    if ":" not in model:
        return None, None, None
    provider = model.split(":", 1)[0].strip().lower()
    if provider == "openai":
        return "langchain_openai", "langchain-openai", "OPENAI_API_KEY"
    if provider == "anthropic":
        return "langchain_anthropic", "langchain-anthropic", "ANTHROPIC_API_KEY"
    if provider in {"google", "google_genai", "gemini"}:
        return "langchain_google_genai", "langchain-google-genai", "GOOGLE_API_KEY"
    return None, None, None


@dataclass(slots=True)
class PreflightResult:
    errors: list[str]
    warnings: list[str]


@dataclass(slots=True)
class ContextBuildResult:
    context_text: str
    loaded_files: list[str]
    skipped_files: list[str]
    truncated_files: list[str]


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LangGraph RLM repo-improver with optional LangSmith tracing")
    parser.add_argument(
        "--files",
        nargs="*",
        default=list(_DEFAULT_FILES),
        help="Workspace-relative files to load into Aleph context",
    )
    parser.add_argument(
        "--query",
        default=_DEFAULT_QUERY,
        help="Analysis prompt sent after context load",
    )
    parser.add_argument(
        "--context-id",
        default="repo_improver",
        help="Aleph context id used for this run",
    )
    parser.add_argument(
        "--thread-id",
        default="repo-improver-thread",
        help="LangGraph checkpoint thread id",
    )
    parser.add_argument(
        "--max-chars-per-file",
        type=int,
        default=20_000,
        help="Maximum characters loaded per file",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MODEL", "openai:gpt-4.1-mini"),
        help="LangChain model spec",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and preview context payload without invoking model/tools",
    )

    env_ls_default = _env_truthy("LANGSMITH_TRACING")
    parser.add_argument(
        "--langsmith",
        dest="langsmith",
        action="store_true",
        default=env_ls_default,
        help="Enable LangSmith tracing context (default follows LANGSMITH_TRACING)",
    )
    parser.add_argument(
        "--no-langsmith",
        dest="langsmith",
        action="store_false",
        help="Disable LangSmith tracing context",
    )
    parser.add_argument(
        "--langsmith-project",
        default=os.environ.get("LANGSMITH_PROJECT", "aleph-rlm"),
        help="LangSmith project name",
    )
    parser.add_argument(
        "--langsmith-tags",
        default="aleph,langgraph,repo-improver",
        help="Comma-separated LangSmith tags",
    )

    return parser.parse_args()


def _env_config(model: str) -> AlephRLMConfig:
    transport_env = os.environ.get("ALEPH_MCP_TRANSPORT", "stdio").strip().lower()
    if transport_env not in {"stdio", "streamable_http", "http"}:
        transport_env = "stdio"
    args_env = os.environ.get("ALEPH_MCP_ARGS", "").strip()
    args = tuple(args_env.split()) if args_env else ()

    transport = cast(Literal["stdio", "streamable_http", "http"], transport_env)

    return AlephRLMConfig(
        transport=transport,
        server_url=os.environ.get("ALEPH_MCP_URL", "http://127.0.0.1:8765/mcp"),
        command=os.environ.get("ALEPH_MCP_COMMAND", "aleph"),
        args=args,
        model=model,
    )


def _build_repo_context(paths: list[str], max_chars_per_file: int) -> ContextBuildResult:
    loaded: list[str] = []
    skipped: list[str] = []
    truncated: list[str] = []
    sections: list[str] = []

    for raw_path in paths:
        path = Path(raw_path).resolve()
        if not path.exists() or not path.is_file():
            skipped.append(raw_path)
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped.append(raw_path)
            continue

        if len(text) > max_chars_per_file:
            text = text[:max_chars_per_file]
            truncated.append(raw_path)

        loaded.append(raw_path)
        sections.append(
            "\n".join(
                [
                    f"### FILE: {raw_path}",
                    "```text",
                    text,
                    "```",
                ]
            )
        )

    return ContextBuildResult(
        context_text="\n\n".join(sections),
        loaded_files=loaded,
        skipped_files=skipped,
        truncated_files=truncated,
    )


def _build_load_prompt(context_id: str, context_text: str) -> str:
    return (
        "Call the Aleph tool `load_context` with these exact arguments and no extra commentary:\n"
        f"- context_id: {json.dumps(context_id)}\n"
        f"- context: {json.dumps(context_text)}"
    )


def _preflight(args: argparse.Namespace) -> PreflightResult:
    errors: list[str] = []
    warnings: list[str] = []

    if args.max_chars_per_file <= 0:
        errors.append("--max-chars-per-file must be greater than 0")

    if not args.files:
        errors.append("At least one file path is required via --files")

    if args.langsmith:
        if not _module_available("langsmith"):
            errors.append("LangSmith tracing enabled but `langsmith` package is not installed")
        if not os.environ.get("LANGSMITH_API_KEY"):
            warnings.append("LANGSMITH_API_KEY is not set; traces may not upload to smith.langchain.com")

    if args.dry_run:
        return PreflightResult(errors=errors, warnings=warnings)

    required_modules = [
        "langchain",
        "langgraph",
        "langchain_mcp_adapters",
    ]
    missing = [name for name in required_modules if not _module_available(name)]
    if missing:
        errors.append(
            "Missing runtime dependencies: " + ", ".join(missing) + ". Install with: pip install -e \".[mcp,langgraph]\""
        )

    provider_module, provider_pkg, provider_key = _provider_requirements(args.model)
    if provider_module and not _module_available(provider_module):
        errors.append(
            f"Model '{args.model}' requires `{provider_pkg}`. Install with: pip install {provider_pkg}"
        )
    if provider_key and not os.environ.get(provider_key):
        errors.append(
            f"Model '{args.model}' requires {provider_key}. Set it before running or choose a different --model."
        )

    return PreflightResult(errors=errors, warnings=warnings)


def _langsmith_context(
    *,
    enabled: bool,
    project: str,
    tags: list[str],
    metadata: dict[str, Any],
) -> Any:
    if not enabled:
        return nullcontext()

    try:
        import langsmith as ls
    except Exception:
        return nullcontext()

    return ls.tracing_context(
        enabled=True,
        project_name=project,
        tags=tags,
        metadata=metadata,
    )


def _message_field(message: Any, field: str) -> Any:
    if isinstance(message, dict):
        return message.get(field)
    return getattr(message, field, None)


def _extract_final_answer(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)

    final_answer = result.get("final_answer")
    if isinstance(final_answer, str) and final_answer.strip():
        return final_answer

    messages = result.get("messages", [])
    if not isinstance(messages, list):
        return str(result)

    for message in reversed(messages):
        role = _message_field(message, "role")
        msg_type = _message_field(message, "type")
        if role in {"assistant", "ai"} or msg_type in {"assistant", "ai"}:
            content = _message_field(message, "content")
            if isinstance(content, str):
                return content
    return str(result)


async def _run() -> None:
    args = _parse_args()
    preflight = _preflight(args)

    for warning in preflight.warnings:
        print(f"[WARN] {warning}")

    if preflight.errors:
        for error in preflight.errors:
            print(f"[ERROR] {error}")
        raise SystemExit(1)

    context_payload = _build_repo_context(args.files, args.max_chars_per_file)

    print(f"Loaded files: {len(context_payload.loaded_files)}")
    if context_payload.loaded_files:
        for path in context_payload.loaded_files:
            print(f"- {path}")
    if context_payload.skipped_files:
        print("Skipped files:")
        for path in context_payload.skipped_files:
            print(f"- {path}")
    if context_payload.truncated_files:
        print("Truncated files:")
        for path in context_payload.truncated_files:
            print(f"- {path}")

    if args.dry_run:
        print("\nDry run complete. No model/tool invocation executed.")
        return

    if not context_payload.context_text.strip():
        print("[ERROR] No readable files were loaded; aborting invocation.")
        raise SystemExit(1)

    tags = [tag.strip() for tag in args.langsmith_tags.split(",") if tag.strip()]
    metadata = {
        "integration": "aleph_langgraph_rlm",
        "mode": "repo_improver",
        "files_loaded": context_payload.loaded_files,
        "files_skipped": context_payload.skipped_files,
        "thread_id": args.thread_id,
    }

    config = _env_config(args.model)
    try:
        graph = await build_rlm_default_graph(config)
    except Exception as exc:
        print(f"[ERROR] Failed to initialize LangGraph RLM graph: {exc}")
        raise SystemExit(1)

    with _langsmith_context(
        enabled=bool(args.langsmith),
        project=args.langsmith_project,
        tags=tags,
        metadata=metadata,
    ):
        load_prompt = _build_load_prompt(args.context_id, context_payload.context_text)
        try:
            await invoke_rlm(graph, load_prompt, thread_id=args.thread_id, config=config)
        except Exception as exc:
            print(f"[ERROR] Failed during context load invocation: {exc}")
            raise SystemExit(1)

        try:
            result = await invoke_rlm(
                graph,
                args.query,
                thread_id=args.thread_id,
                config=config,
            )
        except Exception as exc:
            print(f"[ERROR] Failed during analysis invocation: {exc}")
            raise SystemExit(1)

    tool_trace = collect_tool_trace(result)
    answer = _extract_final_answer(result)

    print("\n=== Tool Trace ===")
    if tool_trace:
        for name in tool_trace:
            print(f"- {name}")
    else:
        print("(no tool activity captured)")

    print("\n=== Improvement Suggestions ===")
    print(answer)


if __name__ == "__main__":
    asyncio.run(_run())

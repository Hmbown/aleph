"""Aleph full CLI runner (alef).

Run the full RLM loop against a provided context from files, stdin, or a literal string.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence, cast

from .config import AlephConfig, create_aleph
from .types import AlephResponse, ContextCollection, ContextType, ContentFormat, JSONValue
from .utils.logging import trajectory_to_json
from .mcp.io_utils import _detect_format, _load_text_from_path

__all__ = ["main"]


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _die(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 1


def _format_label(fmt: ContentFormat) -> str:
    if fmt == ContentFormat.JSON:
        return "json"
    if fmt == ContentFormat.JSONL:
        return "jsonl"
    return "text"


def _parse_context_text(text: str, fmt: str, source: str | None = None) -> ContextType:
    if fmt == "json":
        try:
            return cast(JSONValue, json.loads(text))
        except json.JSONDecodeError as exc:
            label = f" from {source}" if source else ""
            raise ValueError(f"Invalid JSON{label}: {exc}") from exc
    if fmt == "jsonl":
        items: list[JSONValue] = []
        for idx, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                items.append(cast(JSONValue, json.loads(line)))
            except json.JSONDecodeError as exc:
                label = f" in {source}" if source else ""
                raise ValueError(f"Invalid JSONL line {idx}{label}: {exc}") from exc
        return items
    return text


def _resolve_format(
    override: str,
    detected: ContentFormat | None = None,
    text: str | None = None,
) -> str:
    if override != "auto":
        return override
    if detected is not None:
        return _format_label(detected)
    if text is not None:
        return _format_label(_detect_format(text))
    return "text"


def _load_context_from_files(
    paths: Iterable[str],
    format_override: str,
    max_bytes: int,
    timeout_seconds: float,
) -> tuple[ContextType, list[str]]:
    items: list[tuple[str, ContextType]] = []
    warnings: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise ValueError(f"Context file not found: {path}")
        if path.is_dir():
            raise ValueError(f"Context path is a directory (expected file): {path}")
        text, detected_format, warning = _load_text_from_path(path, max_bytes, timeout_seconds)
        fmt = _resolve_format(format_override, detected=detected_format)
        context = _parse_context_text(text, fmt, source=str(path))
        items.append((str(path), context))
        if warning:
            warnings.append(f"{path}: {warning}")
    if len(items) == 1:
        return items[0][1], warnings
    return ContextCollection(items=items), warnings


def _load_context(args: argparse.Namespace) -> tuple[ContextType, list[str]]:
    if args.context_files:
        return _load_context_from_files(
            args.context_files,
            args.context_format,
            args.max_context_bytes,
            args.extract_timeout,
        )

    if args.context_stdin:
        text = sys.stdin.read()
        fmt = _resolve_format(args.context_format, text=text)
        return _parse_context_text(text, fmt), []

    if args.context is not None:
        fmt = _resolve_format(args.context_format, text=args.context)
        return _parse_context_text(args.context, fmt), []

    return "", []


def _apply_overrides(config: AlephConfig, args: argparse.Namespace) -> None:
    if args.provider:
        config.provider = args.provider
    if args.model:
        config.root_model = args.model
    if args.sub_model:
        config.sub_model = args.sub_model
    if args.api_key:
        config.api_key = args.api_key
    if args.max_iterations is not None:
        config.max_iterations = args.max_iterations
    if args.max_depth is not None:
        config.max_depth = args.max_depth
    if args.max_tokens is not None:
        config.max_tokens = args.max_tokens
    if args.max_wall_time is not None:
        config.max_wall_time_seconds = args.max_wall_time
    if args.max_sub_queries is not None:
        config.max_sub_queries = args.max_sub_queries
    if args.context_var:
        config.context_var_name = args.context_var
    if args.system_prompt:
        config.system_prompt = args.system_prompt
    if args.no_cache:
        config.enable_caching = False
    if args.no_trajectory:
        config.log_trajectory = False


def _response_payload(
    response: AlephResponse,
    include_trajectory: bool,
    prompt: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "success": response.success,
        "answer": response.answer,
        "error": response.error,
        "error_type": response.error_type,
        "total_iterations": response.total_iterations,
        "max_depth_reached": response.max_depth_reached,
        "total_tokens": response.total_tokens,
        "total_cost_usd": response.total_cost_usd,
        "wall_time_seconds": response.wall_time_seconds,
    }
    if prompt is not None:
        payload["prompt"] = prompt
    if include_trajectory:
        payload["trajectory"] = trajectory_to_json(response.trajectory)
    return payload


def _print_response(
    response: AlephResponse,
    args: argparse.Namespace,
    prompt: str | None = None,
) -> None:
    if args.json:
        payload = _response_payload(response, args.include_trajectory, prompt=prompt)
        print(json.dumps(payload, ensure_ascii=False))
        return

    if response.answer:
        print(response.answer)
    if not response.success and response.error:
        print(response.error, file=sys.stderr)


def _run_command(args: argparse.Namespace) -> int:
    config = AlephConfig.from_file(args.config) if args.config else AlephConfig.from_env()
    _apply_overrides(config, args)
    aleph = create_aleph(config)

    try:
        context, warnings = _load_context(args)
    except ValueError as exc:
        return _die(str(exc))

    for warning in warnings:
        _warn(warning)

    response = aleph.complete_sync(args.prompt, context, temperature=args.temperature)
    _print_response(response, args, prompt=args.prompt)
    return 0 if response.success else 1


def _shell_command(args: argparse.Namespace) -> int:
    config = AlephConfig.from_file(args.config) if args.config else AlephConfig.from_env()
    _apply_overrides(config, args)
    aleph = create_aleph(config)

    try:
        context, warnings = _load_context(args)
    except ValueError as exc:
        return _die(str(exc))

    for warning in warnings:
        _warn(warning)

    exit_code = 0
    while True:
        try:
            prompt = input("alef> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            break
        response = aleph.complete_sync(prompt, context, temperature=args.temperature)
        _print_response(response, args, prompt=prompt)
        if not response.success:
            exit_code = 1
    return exit_code


def _serve_command(args: argparse.Namespace) -> int:
    from .mcp.local_server import main as serve_main

    sys.argv = ["aleph", *args.args]
    serve_main()
    return 0


def _add_shared_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to Aleph config file (YAML or JSON).",
    )
    parser.add_argument("--provider", type=str, default=None, help="Provider override.")
    parser.add_argument("--model", type=str, default=None, help="Root model override.")
    parser.add_argument("--sub-model", type=str, default=None, help="Sub-model override.")
    parser.add_argument("--api-key", type=str, default=None, help="API key override.")
    parser.add_argument("--max-iterations", type=int, default=None, help="Iteration limit.")
    parser.add_argument("--max-depth", type=int, default=None, help="Max recursion depth.")
    parser.add_argument("--max-tokens", type=int, default=None, help="Token budget.")
    parser.add_argument("--max-wall-time", type=float, default=None, help="Wall-time budget (seconds).")
    parser.add_argument("--max-sub-queries", type=int, default=None, help="Sub-query budget.")
    parser.add_argument("--temperature", type=float, default=None, help="Sampling temperature.")
    parser.add_argument("--system-prompt", type=str, default=None, help="Override system prompt template.")
    parser.add_argument("--context-var", type=str, default=None, help="Context variable name in the REPL.")
    parser.add_argument("--no-cache", action="store_true", help="Disable sub-query caching.")
    parser.add_argument("--no-trajectory", action="store_true", help="Disable trajectory logging.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    parser.add_argument(
        "--include-trajectory",
        action="store_true",
        help="Include full trajectory in JSON output.",
    )


def _add_context_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--context",
        type=str,
        default=None,
        help="Context string literal.",
    )
    group.add_argument(
        "--context-file",
        dest="context_files",
        action="append",
        help="Context file path (repeatable).",
    )
    group.add_argument(
        "--context-stdin",
        action="store_true",
        help="Read context from stdin.",
    )
    parser.add_argument(
        "--context-format",
        type=str,
        choices=["auto", "text", "json", "jsonl"],
        default="auto",
        help="Context parsing format.",
    )
    parser.add_argument(
        "--max-context-bytes",
        type=int,
        default=1_000_000_000,
        help="Maximum context file size in bytes.",
    )
    parser.add_argument(
        "--extract-timeout",
        type=float,
        default=30.0,
        help="Timeout for document extraction (seconds).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aleph RLM CLI runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a single Aleph query")
    run_parser.add_argument("prompt", type=str, help="Query to run")
    _add_shared_options(run_parser)
    _add_context_options(run_parser)

    shell_parser = subparsers.add_parser("shell", help="Interactive prompt loop")
    _add_shared_options(shell_parser)
    _add_context_options(shell_parser)

    serve_parser = subparsers.add_parser("serve", help="Run MCP server (alias for `aleph`)")
    serve_parser.add_argument("args", nargs=argparse.REMAINDER)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _run_command(args)
    if args.command == "shell":
        return _shell_command(args)
    if args.command == "serve":
        return _serve_command(args)

    return _die(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

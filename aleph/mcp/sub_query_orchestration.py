"""Sub-query and sub-Aleph orchestration helpers for the local MCP server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..config import AlephConfig
from ..core import Aleph
from ..observability import traced_span
from ..prompts.system import DEFAULT_SYSTEM_PROMPT
from ..providers.registry import get_provider
from ..sub_query import detect_backend
from ..sub_query.api_backend import run_api_sub_query
from ..sub_query.cli_backend import CLI_BACKENDS, run_cli_sub_query
from ..sub_query.codex_mcp_backend import (
    build_codex_mcp_tool_call,
    compose_sub_query_prompt,
    extract_codex_mcp_result_text,
    suppress_mcp_notification_validation_logs,
)
from ..sub_query.config import (
    resolve_codex_mode,
    resolve_codex_model,
    resolve_codex_profile,
    resolve_codex_reasoning_effort,
)
from ..types import AlephResponse, ContentFormat, ContextMetadata
from .env_utils import _get_env_bool, _get_env_int
from .formatting import _to_jsonable
from .remote_servers import register_remote_server
from .session import _Evidence, _analyze_text_context as _fallback_analyze_text_context

if TYPE_CHECKING:
    from .local_server import AlephMCPServerLocal


__all__ = [
    "build_sub_aleph_cli_prompt",
    "ensure_internal_codex_mcp_server",
    "ensure_streamable_http_server",
    "extract_final_answer",
    "format_streamable_http_url",
    "normalize_streamable_http_path",
    "run_internal_codex_mcp_query",
    "run_streamable_http_server",
    "run_sub_aleph",
    "run_sub_query",
    "wait_for_streamable_http_ready",
]


_FINAL_RE = re.compile(r"FINAL\((.*?)\)", re.DOTALL)
_FINAL_VAR_RE = re.compile(r"FINAL_VAR\((.*?)\)", re.DOTALL)
_SHARED_SESSION_BACKENDS = {"claude", "codex", "gemini", "kimi"}


def extract_final_answer(text: str) -> tuple[str, bool]:
    match = _FINAL_RE.search(text)
    if match:
        return match.group(1).strip(), True
    match_var = _FINAL_VAR_RE.search(text)
    if match_var:
        raw = match_var.group(1).strip()
        if len(raw) >= 2 and ((raw[0] == raw[-1] == '"') or (raw[0] == raw[-1] == "'")):
            raw = raw[1:-1].strip()
        return raw, True
    return text.strip(), False


def build_sub_aleph_cli_prompt(
    *,
    query: str,
    context_slice: str,
    context_format: ContentFormat,
    cfg: AlephConfig,
    analyze_text_context: Callable[[str, ContentFormat], ContextMetadata],
) -> str:
    meta = analyze_text_context(context_slice, context_format)
    system_template = cfg.system_prompt or DEFAULT_SYSTEM_PROMPT
    system_prompt = system_template.format(
        query=query,
        context_var=cfg.context_var_name,
        context_format=meta.format.value,
        context_size_chars=meta.size_chars,
        context_size_lines=meta.size_lines,
        context_size_tokens=meta.size_tokens_estimate,
        context_preview="[OMITTED FOR CONTEXT ISOLATION]",
        structure_hint=meta.structure_hint or "N/A",
    )
    instructions = (
        "SINGLE-SHOT MODE (no live Python REPL in this call):\n"
        "- Do not output code blocks.\n"
        "- Answer directly and wrap the final answer in FINAL(...).\n"
    )
    return f"{system_prompt}\n\n{instructions}\nQUERY:\n{query}"


def normalize_streamable_http_path(path: str) -> str:
    if not path:
        return "/mcp"
    return path if path.startswith("/") else f"/{path}"


def format_streamable_http_url(host: str, port: int, path: str) -> str:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{connect_host}:{port}{path}"


async def wait_for_streamable_http_ready(
    owner: "AlephMCPServerLocal",
    host: str,
    port: int,
    timeout_seconds: float = 2.0,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host

    while time.monotonic() < deadline:
        if owner._streamable_http_task and owner._streamable_http_task.done():
            exc = owner._streamable_http_task.exception()
            if exc:
                return False, f"Streamable HTTP server failed to start: {exc}"
            return False, "Streamable HTTP server stopped unexpectedly."
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(connect_host, port),
                timeout=0.2,
            )
            writer.close()
            await writer.wait_closed()
            return True, ""
        except Exception:
            await asyncio.sleep(0.05)

    return (
        False,
        f"Timed out waiting for streamable HTTP server on {connect_host}:{port}.",
    )


async def run_streamable_http_server(
    owner: "AlephMCPServerLocal",
    host: str,
    port: int,
) -> None:
    try:
        import uvicorn
    except Exception as exc:
        raise RuntimeError(
            "uvicorn is required for streamable HTTP transport. "
            "Install with: pip install uvicorn"
        ) from exc

    app = owner.server.streamable_http_app()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def ensure_streamable_http_server(
    owner: "AlephMCPServerLocal",
    host: str,
    port: int,
    path: str,
) -> tuple[bool, str]:
    normalized_path = normalize_streamable_http_path(path)
    async with owner._streamable_http_lock:
        if owner._streamable_http_task and not owner._streamable_http_task.done():
            url = owner._streamable_http_url or format_streamable_http_url(
                host,
                port,
                normalized_path,
            )
            return True, url
        if owner._streamable_http_task and owner._streamable_http_task.done():
            owner._streamable_http_task = None
            owner._streamable_http_url = None

        owner.server.settings.host = host
        owner.server.settings.port = port
        owner.server.settings.streamable_http_path = normalized_path

        owner._streamable_http_task = asyncio.create_task(
            owner._run_streamable_http_server(host, port)
        )
        owner._streamable_http_host = host
        owner._streamable_http_port = port
        owner._streamable_http_path = normalized_path
        owner._streamable_http_url = format_streamable_http_url(
            host,
            port,
            normalized_path,
        )

    ok, err = await owner._wait_for_streamable_http_ready(host, port)
    if not ok:
        return False, err
    return True, owner._streamable_http_url or format_streamable_http_url(
        host,
        port,
        normalized_path,
    )


async def ensure_internal_codex_mcp_server(
    owner: "AlephMCPServerLocal",
    cwd: Path | None,
) -> str:
    server_id = "__aleph_internal_codex__"
    handle = owner._remote_servers.get(server_id)
    if handle is None:
        handle = register_remote_server(
            owner._remote_servers,
            server_id,
            command="codex",
            args=["mcp-server", "-c", "mcp_servers={}"],
            cwd=cwd,
            allow_tools=["codex", "codex-reply"],
        )
    elif handle.cwd != cwd:
        await owner._reset_remote_server_handle(handle)
        handle.cwd = cwd

    with suppress_mcp_notification_validation_logs():
        ok, res = await owner._ensure_remote_server(server_id)
    if not ok:
        raise RuntimeError(str(res))
    return server_id


async def run_internal_codex_mcp_query(
    owner: "AlephMCPServerLocal",
    *,
    prompt: str,
    context_slice: str | None,
    cwd: Path | None,
    mcp_server_url: str | None,
    mcp_server_name: str,
    thread_id: str | None = None,
) -> tuple[bool, str, str | None]:
    full_prompt = compose_sub_query_prompt(prompt, context_slice)

    tool_name, arguments = build_codex_mcp_tool_call(
        prompt=full_prompt,
        cwd=cwd,
        mcp_server_url=mcp_server_url,
        mcp_server_name=mcp_server_name,
        trust_mcp_server=True,
        model=resolve_codex_model(owner.sub_query_config.codex_model),
        reasoning_effort=resolve_codex_reasoning_effort(
            owner.sub_query_config.codex_reasoning_effort
        ),
        profile=resolve_codex_profile(owner.sub_query_config.codex_profile),
        thread_id=thread_id,
    )

    try:
        server_id = await owner._ensure_internal_codex_mcp_server(cwd)
    except Exception as exc:
        return False, f"Failed to start internal Codex MCP server: {exc}", None

    with suppress_mcp_notification_validation_logs():
        ok, result = await owner._remote_call_tool(
            server_id,
            tool_name,
            arguments,
            timeout_seconds=owner.sub_query_config.cli_timeout_seconds,
        )
    if not ok:
        return False, str(result), None

    output, resolved_thread_id = extract_codex_mcp_result_text(result)
    if not output:
        output = json.dumps(_to_jsonable(result), ensure_ascii=True)

    if len(output) > owner.sub_query_config.cli_max_output_chars:
        output = (
            output[: owner.sub_query_config.cli_max_output_chars]
            + "\n...[truncated]"
        )

    return True, output, resolved_thread_id


async def _prepare_cli_shared_session(
    owner: "AlephMCPServerLocal",
    *,
    prompt: str,
    context_id: str,
    resolved_backend: str,
) -> tuple[bool, str, str | None, str]:
    mcp_server_url = None
    server_name = "aleph_shared"
    share_session = _get_env_bool("ALEPH_SUB_QUERY_SHARE_SESSION", False)

    if share_session and resolved_backend in _SHARED_SESSION_BACKENDS:
        host = os.environ.get("ALEPH_SUB_QUERY_HTTP_HOST", "127.0.0.1")
        port = _get_env_int("ALEPH_SUB_QUERY_HTTP_PORT", 8765)
        path = os.environ.get("ALEPH_SUB_QUERY_HTTP_PATH", "/mcp")
        server_name = (
            os.environ.get(
                "ALEPH_SUB_QUERY_MCP_SERVER_NAME",
                "aleph_shared",
            ).strip()
            or "aleph_shared"
        )
        ok, url_or_err = await owner._ensure_streamable_http_server(host, port, path)
        if not ok:
            return False, url_or_err, None, server_name
        mcp_server_url = url_or_err
        prompt = (
            f"{prompt}\n\n"
            f"[MCP tools are available via the live Aleph server. "
            f"Use context_id={context_id!r} when calling tools. "
            f"Tools are prefixed with `mcp__{server_name}__`.]"
        )

    return True, prompt, mcp_server_url, server_name


async def run_sub_query(
    owner: "AlephMCPServerLocal",
    *,
    prompt: str,
    context_slice: str | None,
    context_id: str,
    backend: str,
    validation_regex: str | None = None,
    max_retries: int | None = None,
    retry_prompt: str | None = None,
) -> tuple[bool, str, bool, str]:
    session = owner._sessions.get(context_id)
    if session:
        session.iterations += 1

    truncated = False
    if context_slice and len(context_slice) > owner.sub_query_config.max_context_chars:
        context_slice = context_slice[: owner.sub_query_config.max_context_chars]
        truncated = True

    resolved_backend = backend
    if backend == "auto":
        resolved_backend = detect_backend(owner.sub_query_config)

    allowed_backends = {"auto", "api", *CLI_BACKENDS}
    if resolved_backend not in allowed_backends:
        allowed_list = ", ".join(sorted(allowed_backends))
        return (
            False,
            f"Unsupported backend '{resolved_backend}'. Choose from: {allowed_list}.",
            truncated,
            resolved_backend,
        )

    resolved_validation_regex = validation_regex
    if resolved_validation_regex is None:
        resolved_validation_regex = (
            owner.sub_query_config.validation_regex
            or os.environ.get("ALEPH_SUB_QUERY_VALIDATION_REGEX")
        )
    if resolved_validation_regex is not None:
        resolved_validation_regex = resolved_validation_regex.strip()
        if not resolved_validation_regex:
            resolved_validation_regex = None

    resolved_max_retries = (
        owner.sub_query_config.max_retries if max_retries is None else max_retries
    )
    if max_retries is None:
        resolved_max_retries = _get_env_int(
            "ALEPH_SUB_QUERY_MAX_RETRIES", resolved_max_retries
        )

    resolved_retry_prompt = (
        owner.sub_query_config.retry_prompt if retry_prompt is None else retry_prompt
    )
    if retry_prompt is None:
        env_retry_prompt = os.environ.get("ALEPH_SUB_QUERY_RETRY_PROMPT")
        if env_retry_prompt:
            resolved_retry_prompt = env_retry_prompt

    validation_re: re.Pattern[str] | None = None
    if resolved_validation_regex:
        try:
            validation_re = re.compile(resolved_validation_regex, re.MULTILINE)
        except re.error as exc:
            return (
                False,
                f"Invalid validation regex: {exc}",
                truncated,
                resolved_backend,
            )

    attempt = 0
    base_prompt = prompt
    prompt_for_attempt = base_prompt
    codex_thread_id: str | None = None
    with traced_span(
        "aleph.sub_query",
        {
            "aleph.context_id": context_id,
            "aleph.sub_query.backend.requested": backend,
            "aleph.sub_query.backend.resolved": resolved_backend,
            "aleph.sub_query.context_chars": len(context_slice or ""),
            "aleph.sub_query.context_truncated": truncated,
            "aleph.sub_query.validation_enabled": bool(resolved_validation_regex),
        },
    ) as span:
        success = False
        output = ""
        try:
            while True:
                run_prompt = prompt_for_attempt
                if resolved_backend in CLI_BACKENDS:
                    ok, prepared_prompt, mcp_server_url, server_name = (
                        await _prepare_cli_shared_session(
                            owner,
                            prompt=run_prompt,
                            context_id=context_id,
                            resolved_backend=resolved_backend,
                        )
                    )
                    if not ok:
                        return (
                            False,
                            f"Failed to start streamable HTTP server: {prepared_prompt}",
                            truncated,
                            resolved_backend,
                        )
                    run_prompt = prepared_prompt
                    cwd = (
                        owner.action_config.workspace_root
                        if owner.action_config.enabled
                        else None
                    )
                    if (
                        resolved_backend == "codex"
                        and resolve_codex_mode(owner.sub_query_config.codex_mode)
                        == "mcp"
                    ):
                        success, output, codex_thread_id = (
                            await owner._run_internal_codex_mcp_query(
                                prompt=run_prompt,
                                context_slice=context_slice,
                                cwd=cwd,
                                mcp_server_url=mcp_server_url,
                                mcp_server_name=server_name,
                                thread_id=codex_thread_id,
                            )
                        )
                    else:
                        success, output = await run_cli_sub_query(
                            prompt=run_prompt,
                            context_slice=context_slice,
                            backend=resolved_backend,  # type: ignore[arg-type]
                            timeout=owner.sub_query_config.cli_timeout_seconds,
                            cwd=cwd,
                            max_output_chars=owner.sub_query_config.cli_max_output_chars,
                            max_context_chars=owner.sub_query_config.max_context_chars,
                            mcp_server_url=mcp_server_url,
                            mcp_server_name=server_name,
                            trust_mcp_server=True,
                            claude_model=owner.sub_query_config.claude_model,
                            claude_effort=owner.sub_query_config.claude_effort,
                            codex_mode=owner.sub_query_config.codex_mode,
                            codex_model=owner.sub_query_config.codex_model,
                            codex_reasoning_effort=owner.sub_query_config.codex_reasoning_effort,
                            codex_profile=owner.sub_query_config.codex_profile,
                        )
                else:
                    success, output = await run_api_sub_query(
                        prompt=run_prompt,
                        context_slice=context_slice,
                        model=owner.sub_query_config.api_model,
                        api_key_env=owner.sub_query_config.api_key_env,
                        api_base_url_env=owner.sub_query_config.api_base_url_env,
                        api_model_env=owner.sub_query_config.api_model_env,
                        timeout=owner.sub_query_config.api_timeout_seconds,
                        system_prompt=owner.sub_query_config.system_prompt
                        if owner.sub_query_config.include_system_prompt
                        else None,
                        max_context_chars=owner.sub_query_config.max_context_chars,
                    )

                if not success:
                    break

                if validation_re and not validation_re.search(output):
                    if attempt >= resolved_max_retries:
                        success = False
                        output = (
                            f"Output failed validation regex {resolved_validation_regex!r} "
                            f"after {attempt + 1} attempt(s). Last output: {output}"
                        )
                        break
                    attempt += 1
                    prompt_for_attempt = (
                        f"{base_prompt}\n\n"
                        f"{resolved_retry_prompt}\n"
                        f"Required format regex: {resolved_validation_regex}"
                    )
                    continue

                break
        except Exception as exc:
            span.record_exception(exc)
            success = False
            output = f"{type(exc).__name__}: {exc}"

        span.set_attribute("aleph.sub_query.success", success)
        span.set_attribute("aleph.sub_query.attempts", attempt + 1)
        span.set_attribute("aleph.sub_query.output_chars", len(output))

        if session:
            note_parts = [f"backend={resolved_backend}"]
            if resolved_validation_regex:
                note_parts.append(f"validation={resolved_validation_regex!r}")
                if attempt:
                    note_parts.append(f"retries={attempt}")
            if truncated:
                note_parts.append("truncated_context")
            session.evidence.append(
                _Evidence(
                    source="sub_query",
                    line_range=None,
                    pattern=None,
                    snippet=output[:200] if success else f"[ERROR] {output[:150]}",
                    note=" ".join(note_parts),
                )
            )
            session.information_gain.append(1 if success else 0)

        return success, output, truncated, resolved_backend


async def run_sub_aleph(
    owner: "AlephMCPServerLocal",
    *,
    query: str,
    context_slice: str | None,
    context_id: str,
    current_depth: int = 1,
    root_model: str | None = None,
    sub_model: str | None = None,
    max_depth: int | None = None,
    max_iterations: int | None = None,
    max_tokens: int | None = None,
    max_sub_queries: int | None = None,
    max_wall_time_seconds: float | None = None,
    temperature: float | None = None,
    analyze_text_context: Callable[[str, ContentFormat], ContextMetadata] | None = None,
) -> tuple[AlephResponse, dict[str, object]]:
    session = owner._sessions.get(context_id)
    if session:
        session.iterations += 1
        session.max_depth_seen = max(session.max_depth_seen, current_depth)

    cfg = AlephConfig.from_env()
    budget = cfg.to_budget()
    if max_tokens is not None:
        budget.max_tokens = max_tokens
    if max_iterations is not None:
        budget.max_iterations = max_iterations
    if max_depth is not None:
        budget.max_depth = max_depth
    if max_wall_time_seconds is not None:
        budget.max_wall_time_seconds = max_wall_time_seconds
    if max_sub_queries is not None:
        budget.max_sub_queries = max_sub_queries

    resolved_root = root_model or cfg.root_model
    resolved_sub = sub_model or cfg.sub_model or resolved_root

    temp_val = 0.0
    if temperature is not None:
        try:
            temp_val = float(temperature)
        except (TypeError, ValueError):
            temp_val = 0.0

    resolved_backend = detect_backend(owner.sub_query_config)
    truncated_context = False
    start_time = time.perf_counter()
    response: AlephResponse | None = None

    if resolved_backend in CLI_BACKENDS:
        cli_context = context_slice or ""
        if cli_context and len(cli_context) > owner.sub_query_config.max_context_chars:
            cli_context = cli_context[: owner.sub_query_config.max_context_chars]
            truncated_context = True

        context_format = session.meta.format if session else ContentFormat.TEXT
        prompt = build_sub_aleph_cli_prompt(
            query=query,
            context_slice=cli_context,
            context_format=context_format,
            cfg=cfg,
            analyze_text_context=analyze_text_context or _fallback_analyze_text_context,
        )

        mcp_server_url = None
        server_name = "aleph_shared"
        share_session = _get_env_bool("ALEPH_SUB_QUERY_SHARE_SESSION", False)
        if share_session and resolved_backend in _SHARED_SESSION_BACKENDS:
            ok, prepared_prompt, mcp_server_url, server_name = (
                await _prepare_cli_shared_session(
                    owner,
                    prompt=prompt,
                    context_id=context_id,
                    resolved_backend=resolved_backend,
                )
            )
            if not ok:
                response = AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=0,
                    max_depth_reached=0,
                    total_tokens=0,
                    total_cost_usd=0.0,
                    wall_time_seconds=time.perf_counter() - start_time,
                    trajectory=[],
                    error=f"Failed to start streamable HTTP server: {prepared_prompt}",
                    error_type="cli_error",
                )
            else:
                prompt = prepared_prompt

        if mcp_server_url is not None or not share_session:
            try:
                cwd = (
                    owner.action_config.workspace_root
                    if owner.action_config.enabled
                    else None
                )
                if (
                    resolved_backend == "codex"
                    and resolve_codex_mode(owner.sub_query_config.codex_mode) == "mcp"
                ):
                    success, output, _thread_id = await owner._run_internal_codex_mcp_query(
                        prompt=prompt,
                        context_slice=cli_context if cli_context else None,
                        cwd=cwd,
                        mcp_server_url=mcp_server_url,
                        mcp_server_name=server_name,
                    )
                else:
                    success, output = await run_cli_sub_query(
                        prompt=prompt,
                        context_slice=cli_context if cli_context else None,
                        backend=resolved_backend,  # type: ignore[arg-type]
                        timeout=owner.sub_query_config.cli_timeout_seconds,
                        cwd=cwd,
                        max_output_chars=owner.sub_query_config.cli_max_output_chars,
                        max_context_chars=owner.sub_query_config.max_context_chars,
                        mcp_server_url=mcp_server_url,
                        mcp_server_name=server_name,
                        trust_mcp_server=True,
                        claude_model=owner.sub_query_config.claude_model,
                        claude_effort=owner.sub_query_config.claude_effort,
                        codex_mode=owner.sub_query_config.codex_mode,
                        codex_model=owner.sub_query_config.codex_model,
                        codex_reasoning_effort=owner.sub_query_config.codex_reasoning_effort,
                        codex_profile=owner.sub_query_config.codex_profile,
                    )
            except Exception as exc:
                success, output = False, f"{type(exc).__name__}: {exc}"

            wall_time = time.perf_counter() - start_time
            if success:
                answer, _ = extract_final_answer(output)
                if not answer:
                    response = AlephResponse(
                        answer="",
                        success=False,
                        total_iterations=current_depth,
                        max_depth_reached=current_depth,
                        total_tokens=0,
                        total_cost_usd=0.0,
                        wall_time_seconds=wall_time,
                        trajectory=[],
                        error="Empty response from CLI backend",
                        error_type="cli_error",
                    )
                else:
                    response = AlephResponse(
                        answer=answer,
                        success=True,
                        total_iterations=current_depth,
                        max_depth_reached=current_depth,
                        total_tokens=0,
                        total_cost_usd=0.0,
                        wall_time_seconds=wall_time,
                        trajectory=[],
                    )
            else:
                response = AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=current_depth,
                    max_depth_reached=current_depth,
                    total_tokens=0,
                    total_cost_usd=0.0,
                    wall_time_seconds=wall_time,
                    trajectory=[],
                    error=output,
                    error_type="cli_error",
                )
    else:
        try:
            provider = get_provider(cfg.provider, api_key=cfg.api_key)
            runner = Aleph(
                provider=provider,
                root_model=resolved_root,
                sub_model=resolved_sub,
                budget=budget,
                sandbox_config=owner.sandbox_config,
                system_prompt=cfg.system_prompt,
                enable_caching=cfg.enable_caching,
                log_trajectory=cfg.log_trajectory,
            )
            response = await runner.complete(
                query=query,
                context=context_slice or "",
                root_model=resolved_root,
                sub_model=resolved_sub,
                budget=budget,
                temperature=temp_val,
            )
        except Exception as exc:
            response = AlephResponse(
                answer="",
                success=False,
                total_iterations=0,
                max_depth_reached=0,
                total_tokens=0,
                total_cost_usd=0.0,
                wall_time_seconds=0.0,
                trajectory=[],
                error=str(exc),
                error_type="provider_error",
            )

    if response is None:
        response = AlephResponse(
            answer="",
            success=False,
            total_iterations=current_depth,
            max_depth_reached=current_depth,
            total_tokens=0,
            total_cost_usd=0.0,
            wall_time_seconds=time.perf_counter() - start_time,
            trajectory=[],
            error="CLI backend could not start.",
            error_type="cli_error",
        )

    if session:
        note_parts = [
            f"backend={resolved_backend}",
            f"models={resolved_root}/{resolved_sub}",
        ]
        if budget.max_depth is not None:
            note_parts.append(f"max_depth={budget.max_depth}")
        if truncated_context:
            note_parts.append("truncated_context")
        session.evidence.append(
            _Evidence(
                source="sub_aleph",
                line_range=None,
                pattern=None,
                snippet=response.answer[:200]
                if response.success
                else f"[ERROR] {str(response.error)[:150]}",
                note=" ".join(note_parts),
            )
        )
        session.information_gain.append(1 if response.success else 0)

    meta: dict[str, object] = {
        "root_model": resolved_root,
        "sub_model": resolved_sub,
        "budget": budget,
        "temperature": temp_val,
        "backend": resolved_backend,
        "truncated_context": truncated_context,
    }
    return response, meta

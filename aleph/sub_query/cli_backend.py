"""CLI backend for sub-queries.

Spawns local CLI tools as sub-agents for RLM-style recursive reasoning.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal

from .config import (
    resolve_claude_effort,
    resolve_claude_model,
    resolve_codex_model,
    resolve_codex_mode,
    resolve_codex_profile,
    resolve_codex_reasoning_effort,
)
from .codex_mcp_backend import run_codex_mcp_sub_query

__all__ = ["run_cli_sub_query", "CLI_BACKENDS"]


CLI_BACKENDS = ("claude", "codex", "gemini", "kimi")
DEFAULT_MAX_CONTEXT_CHARS = 20_000

_KEEP_MCP_CONFIG_ENV = "ALEPH_SUB_QUERY_KEEP_MCP_CONFIG"
_GEMINI_SANDBOX_ENV = "ALEPH_SUB_QUERY_GEMINI_SANDBOX"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _track_cleanup(path: Path, cleanup_paths: list[Path]) -> None:
    if _env_bool(_KEEP_MCP_CONFIG_ENV, False):
        print(f"[aleph] Keeping MCP config: {path}", file=sys.stderr)
    else:
        cleanup_paths.append(path)


def _gemini_sandbox_enabled() -> bool:
    return _env_bool(_GEMINI_SANDBOX_ENV, False)


def _gemini_base_cmd() -> list[str]:
    sandbox = "true" if _gemini_sandbox_enabled() else "false"
    return ["gemini", "-y", f"--sandbox={sandbox}", "--extensions", ""]


def _extract_tail_json_object(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    decoder = json.JSONDecoder()
    for idx in range(len(text) - 1, -1, -1):
        if text[idx] != "{":
            continue
        candidate = text[idx:].strip()
        try:
            parsed, end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and candidate[end:].strip() == "":
            return parsed
    return None


def _normalize_cli_output(backend: str, output: str) -> str:
    payload = _extract_tail_json_object(output)
    if not payload:
        return output
    if backend == "claude":
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
    if backend == "gemini":
        result = payload.get("response")
        if isinstance(result, str) and result.strip():
            return result.strip()
    return output


async def run_cli_sub_query(
    prompt: str,
    context_slice: str | None = None,
    backend: Literal["claude", "codex", "gemini", "kimi"] = "claude",
    timeout: float = 300.0,
    cwd: Path | None = None,
    max_output_chars: int = 50_000,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    mcp_server_url: str | None = None,
    mcp_server_name: str = "aleph_shared",
    trust_mcp_server: bool = True,
    claude_model: str | None = None,
    claude_effort: str | None = None,
    codex_mode: str | None = None,
    codex_model: str | None = None,
    codex_reasoning_effort: str | None = None,
    codex_profile: str | None = None,
) -> tuple[bool, str]:
    """Spawn a CLI sub-agent and return its response.
    
    Args:
        prompt: The question/task for the sub-agent.
        context_slice: Optional context to include.
        backend: Which CLI tool to use.
        timeout: Timeout in seconds.
        cwd: Working directory for the subprocess.
        max_output_chars: Maximum output characters.
    
    Returns:
        Tuple of (success, output).
    """
    if context_slice and max_context_chars > 0 and len(context_slice) > max_context_chars:
        context_slice = context_slice[:max_context_chars]

    resolved_codex_mode = resolve_codex_mode(codex_mode)
    if backend == "codex" and resolved_codex_mode in {"mcp", "server"}:
        success, output, _thread_id = await run_codex_mcp_sub_query(
            prompt=prompt,
            context_slice=context_slice,
            timeout=timeout,
            cwd=cwd,
            max_output_chars=max_output_chars,
            max_context_chars=max_context_chars,
            mcp_server_url=mcp_server_url,
            mcp_server_name=mcp_server_name,
            trust_mcp_server=trust_mcp_server,
            model=resolve_codex_model(codex_model),
            reasoning_effort=resolve_codex_reasoning_effort(codex_reasoning_effort),
            profile=resolve_codex_profile(codex_profile),
        )
        return success, output

    resolved_claude_model = resolve_claude_model(claude_model)
    resolved_claude_effort = resolve_claude_effort(claude_effort)

    # Build the full prompt. When MCP session sharing is enabled, keep context
    # inside the shared tools boundary instead of embedding raw slices in prompt.
    full_prompt = prompt
    if context_slice and not mcp_server_url:
        full_prompt = f"{prompt}\n\n---\nContext:\n{context_slice}"
    
    # For very long prompts, write to a temp file and pass via stdin/file
    use_tempfile = len(full_prompt) > 10_000
    
    try:
        if use_tempfile:
            return await _run_with_tempfile(
                full_prompt,
                backend,
                timeout,
                cwd,
                max_output_chars,
                mcp_server_url=mcp_server_url,
                mcp_server_name=mcp_server_name,
                trust_mcp_server=trust_mcp_server,
                claude_model=resolved_claude_model,
                claude_effort=resolved_claude_effort,
            )
        else:
            return await _run_with_arg(
                full_prompt,
                backend,
                timeout,
                cwd,
                max_output_chars,
                mcp_server_url=mcp_server_url,
                mcp_server_name=mcp_server_name,
                trust_mcp_server=trust_mcp_server,
                claude_model=resolved_claude_model,
                claude_effort=resolved_claude_effort,
            )
    except FileNotFoundError:
        return False, f"CLI backend '{backend}' not found. Install it or use API fallback."
    except Exception as e:
        return False, f"CLI error: {e}"


def _codex_mcp_overrides(
    mcp_server_url: str,
    mcp_server_name: str,
    trust_mcp_server: bool,
) -> list[str]:
    overrides = [
        "-c",
        f"mcp_servers.{mcp_server_name}.transport={json.dumps('streamable_http')}",
        "-c",
        f"mcp_servers.{mcp_server_name}.url={json.dumps(mcp_server_url)}",
    ]
    if trust_mcp_server:
        overrides.extend(
            [
                "-c",
                f"mcp_servers.{mcp_server_name}.trust=true",
            ]
        )
    return overrides


def _gemini_env_for_mcp(
    mcp_server_url: str,
    mcp_server_name: str,
    trust_mcp_server: bool,
) -> tuple[dict[str, str], Path]:
    env = os.environ.copy()
    payload = {
        "mcpServers": {
            mcp_server_name: {
                "type": "http",
                "url": mcp_server_url,
                "trust": trust_mcp_server,
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        settings_path = Path(f.name)
    env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(settings_path)
    return env, settings_path


def _claude_mcp_config(
    mcp_server_url: str,
    mcp_server_name: str,
) -> Path:
    """Create a temp JSON file with MCP config for Claude CLI.

    Claude CLI uses --mcp-config flag to load MCP servers from JSON files.
    The format is: {"mcpServers": {"name": {"type": "http", "url": "..."}}}
    """
    payload = {
        "mcpServers": {
            mcp_server_name: {
                "type": "http",
                "url": mcp_server_url,
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        return Path(f.name)


async def _run_with_arg(
    prompt: str,
    backend: str,
    timeout: float,
    cwd: Path | None,
    max_output_chars: int,
    mcp_server_url: str | None,
    mcp_server_name: str,
    trust_mcp_server: bool,
    claude_model: str | None = None,
    claude_effort: str | None = None,
) -> tuple[bool, str]:
    """Run CLI with prompt as argument."""
    env: dict[str, str] | None = None
    cleanup_paths: list[Path] = []
    
    if backend == "claude":
        # Claude Code CLI: -p for print mode (non-interactive), --dangerously-skip-permissions to bypass
        mcp_args: list[str] = []
        if mcp_server_url:
            config_path = _claude_mcp_config(mcp_server_url, mcp_server_name)
            _track_cleanup(config_path, cleanup_paths)
            mcp_args = ["--mcp-config", str(config_path), "--strict-mcp-config"]
        model_args: list[str] = []
        if claude_model:
            model_args.extend(["--model", claude_model])
        if claude_effort:
            model_args.extend(["--effort", claude_effort])
        cmd = [
            "claude",
            "-p",
            *model_args,
            *mcp_args,
            prompt,
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "--output-format",
            "json",
        ]
    elif backend == "codex":
        # OpenAI Codex CLI (non-interactive)
        overrides: list[str] = []
        if mcp_server_url:
            overrides = _codex_mcp_overrides(mcp_server_url, mcp_server_name, trust_mcp_server)
        cmd = ["codex", *overrides, "exec", "--skip-git-repo-check", "--full-auto", prompt]
    elif backend == "gemini":
        # Google Gemini CLI: use headless prompt mode instead of positional
        # interactive mode, otherwise the CLI may relaunch into its sandbox path.
        if mcp_server_url:
            env, settings_path = _gemini_env_for_mcp(
                mcp_server_url, mcp_server_name, trust_mcp_server
            )
            _track_cleanup(settings_path, cleanup_paths)
        cmd = [*_gemini_base_cmd(), "-o", "json", "-p", prompt]
    elif backend == "kimi":
        # Kimi CLI: --print --final-message-only for clean text output (implies --yolo)
        mcp_args_kimi: list[str] = []
        if mcp_server_url:
            mcp_args_kimi = ["--mcp-config", json.dumps({"mcpServers": {mcp_server_name: {"type": "http", "url": mcp_server_url}}})]
        cmd = ["kimi", "--print", "--final-message-only", *mcp_args_kimi, "-p", prompt]
    else:
        return False, f"Unknown CLI backend: {backend}"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,  # Prevent subprocess from reading MCP stdio.
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        
        if len(output) > max_output_chars:
            output = output[:max_output_chars] + "\n...[truncated]"
        
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            if output.strip():
                # Non-zero exit but got stdout — include both in error for debugging.
                return False, f"CLI error (exit {proc.returncode}): {err[:500]}\nOutput: {output[:500]}"
            return False, f"CLI error (exit {proc.returncode}): {err[:1000]}"

        return True, _normalize_cli_output(backend, output)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"CLI timeout after {timeout}s"
    finally:
        for path in cleanup_paths:
            try:
                path.unlink()
            except Exception:
                pass


async def _run_with_tempfile(
    prompt: str,
    backend: str,
    timeout: float,
    cwd: Path | None,
    max_output_chars: int,
    mcp_server_url: str | None,
    mcp_server_name: str,
    trust_mcp_server: bool,
    claude_model: str | None = None,
    claude_effort: str | None = None,
) -> tuple[bool, str]:
    """Run CLI with prompt from temp file (for long prompts)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        temp_path = f.name
    
    try:
        env: dict[str, str] | None = None
        cleanup_paths: list[Path] = []

        if backend == "claude":
            # Claude reads from stdin with -p flag
            mcp_args: list[str] = []
            if mcp_server_url:
                config_path = _claude_mcp_config(mcp_server_url, mcp_server_name)
                _track_cleanup(config_path, cleanup_paths)
                mcp_args = ["--mcp-config", str(config_path), "--strict-mcp-config"]
            model_args: list[str] = []
            if claude_model:
                model_args.extend(["--model", claude_model])
            if claude_effort:
                model_args.extend(["--effort", claude_effort])
            cmd = [
                "claude",
                "-p",
                *model_args,
                *mcp_args,
                "--dangerously-skip-permissions",
                "--no-session-persistence",
                "--output-format",
                "json",
            ]
            stdin_data = prompt.encode("utf-8")
        elif backend == "codex":
            # Codex reads prompt from stdin when "-" is passed
            overrides: list[str] = []
            if mcp_server_url:
                overrides = _codex_mcp_overrides(mcp_server_url, mcp_server_name, trust_mcp_server)
            cmd = ["codex", *overrides, "exec", "--skip-git-repo-check", "--full-auto", "-"]
            stdin_data = prompt.encode("utf-8")
        elif backend == "gemini":
            # Gemini headless mode appends stdin to the explicit prompt.
            if mcp_server_url:
                env, settings_path = _gemini_env_for_mcp(
                    mcp_server_url, mcp_server_name, trust_mcp_server
                )
                _track_cleanup(settings_path, cleanup_paths)
            cmd = [*_gemini_base_cmd(), "-o", "json", "-p", ""]
            stdin_data = prompt.encode("utf-8")
        elif backend == "kimi":
            # Kimi: --print --final-message-only for clean text, reads prompt via stdin
            mcp_args_kimi: list[str] = []
            if mcp_server_url:
                mcp_args_kimi = ["--mcp-config", json.dumps({"mcpServers": {mcp_server_name: {"type": "http", "url": mcp_server_url}}})]
            cmd = ["kimi", "--print", "--final-message-only", *mcp_args_kimi]
            stdin_data = prompt.encode("utf-8")
        else:
            return False, f"Unknown CLI backend: {backend}"
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=timeout
            )
            output = stdout.decode("utf-8", errors="replace")
            
            if len(output) > max_output_chars:
                output = output[:max_output_chars] + "\n...[truncated]"
            
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace")
                if output.strip():
                    return False, f"CLI error (exit {proc.returncode}): {err[:500]}\nOutput: {output[:500]}"
                return False, f"CLI error (exit {proc.returncode}): {err[:1000]}"

            return True, _normalize_cli_output(backend, output)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"CLI timeout after {timeout}s"
        finally:
            for path in cleanup_paths:
                try:
                    path.unlink()
                except Exception:
                    pass
    finally:
        # Clean up temp file
        try:
            Path(temp_path).unlink()
        except Exception:
            pass

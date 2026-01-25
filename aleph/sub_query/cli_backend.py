"""CLI backend for sub-queries.

Spawns CLI tools (claude, codex) as sub-agents.
This allows RLM-style recursive reasoning without API keys.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal

__all__ = ["run_cli_sub_query", "CLI_BACKENDS"]


CLI_BACKENDS = ("claude", "codex", "gemini")

_KEEP_MCP_CONFIG_ENV = "ALEPH_SUB_QUERY_KEEP_MCP_CONFIG"


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


async def run_cli_sub_query(
    prompt: str,
    context_slice: str | None = None,
    backend: Literal["claude", "codex", "gemini"] = "claude",
    timeout: float = 120.0,
    cwd: Path | None = None,
    max_output_chars: int = 50_000,
    mcp_server_url: str | None = None,
    mcp_server_name: str = "aleph_shared",
    trust_mcp_server: bool = True,
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
    # Build the full prompt
    full_prompt = prompt
    if context_slice:
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
        cmd = ["claude", "-p", *mcp_args, prompt, "--dangerously-skip-permissions"]
    elif backend == "codex":
        # OpenAI Codex CLI (non-interactive)
        overrides: list[str] = []
        if mcp_server_url:
            overrides = _codex_mcp_overrides(mcp_server_url, mcp_server_name, trust_mcp_server)
        cmd = ["codex", *overrides, "exec", "--skip-git-repo-check", "--full-auto", prompt]
    elif backend == "gemini":
        # Google Gemini CLI: -y for yolo mode (auto-approve all actions)
        if mcp_server_url:
            env, settings_path = _gemini_env_for_mcp(
                mcp_server_url, mcp_server_name, trust_mcp_server
            )
            _track_cleanup(settings_path, cleanup_paths)
        cmd = ["gemini", "-y", prompt]
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
            # Some CLIs write to stderr even on success, check if we got output
            if output.strip():
                return True, output
            return False, f"CLI error (exit {proc.returncode}): {err[:1000]}"
        
        return True, output
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
            cmd = ["claude", "-p", *mcp_args, "--dangerously-skip-permissions"]
            stdin_data = prompt.encode("utf-8")
        elif backend == "codex":
            # Codex reads prompt from stdin when "-" is passed
            overrides: list[str] = []
            if mcp_server_url:
                overrides = _codex_mcp_overrides(mcp_server_url, mcp_server_name, trust_mcp_server)
            cmd = ["codex", *overrides, "exec", "--skip-git-repo-check", "--full-auto", "-"]
            stdin_data = prompt.encode("utf-8")
        elif backend == "gemini":
            # Gemini: -y for yolo mode, pass prompt via stdin
            if mcp_server_url:
                env, settings_path = _gemini_env_for_mcp(
                    mcp_server_url, mcp_server_name, trust_mcp_server
                )
                _track_cleanup(settings_path, cleanup_paths)
            cmd = ["gemini", "-y"]
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
                    return True, output
                return False, f"CLI error (exit {proc.returncode}): {err[:1000]}"
            
            return True, output
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

"""CLI backend for sub-queries.

Spawns CLI tools (claude, codex, aider) as sub-agents.
This allows RLM-style recursive reasoning without API keys.
"""

from __future__ import annotations

import asyncio
import shlex
import tempfile
from pathlib import Path
from typing import Literal

__all__ = ["run_cli_sub_query", "CLI_BACKENDS"]


CLI_BACKENDS = ("claude", "codex", "aider")


async def run_cli_sub_query(
    prompt: str,
    context_slice: str | None = None,
    backend: Literal["claude", "codex", "aider"] = "claude",
    timeout: float = 120.0,
    cwd: Path | None = None,
    max_output_chars: int = 50_000,
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
                full_prompt, backend, timeout, cwd, max_output_chars
            )
        else:
            return await _run_with_arg(
                full_prompt, backend, timeout, cwd, max_output_chars
            )
    except FileNotFoundError:
        return False, f"CLI backend '{backend}' not found. Install it or use API fallback."
    except Exception as e:
        return False, f"CLI error: {e}"


async def _run_with_arg(
    prompt: str,
    backend: str,
    timeout: float,
    cwd: Path | None,
    max_output_chars: int,
) -> tuple[bool, str]:
    """Run CLI with prompt as argument."""
    escaped_prompt = shlex.quote(prompt)
    
    if backend == "claude":
        # Claude Code CLI: -p for print mode (non-interactive), --dangerously-skip-permissions to bypass
        cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    elif backend == "codex":
        # OpenAI Codex CLI
        cmd = ["codex", "-q", prompt]
    elif backend == "aider":
        # Aider CLI
        cmd = ["aider", "--message", prompt, "--yes", "--no-git", "--no-auto-commits"]
    else:
        return False, f"Unknown CLI backend: {backend}"
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    
    try:
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


async def _run_with_tempfile(
    prompt: str,
    backend: str,
    timeout: float,
    cwd: Path | None,
    max_output_chars: int,
) -> tuple[bool, str]:
    """Run CLI with prompt from temp file (for long prompts)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        temp_path = f.name
    
    try:
        if backend == "claude":
            # Claude reads from stdin with -p flag
            cmd = ["claude", "-p", "--dangerously-skip-permissions"]
            stdin_data = prompt.encode("utf-8")
        elif backend == "codex":
            cmd = ["codex", "-q", f"@{temp_path}"]
            stdin_data = None
        elif backend == "aider":
            cmd = ["aider", "--message-file", temp_path, "--yes", "--no-git", "--no-auto-commits"]
            stdin_data = None
        else:
            return False, f"Unknown CLI backend: {backend}"
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
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
        # Clean up temp file
        try:
            Path(temp_path).unlink()
        except Exception:
            pass

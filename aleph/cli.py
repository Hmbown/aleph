"""CLI installer for Aleph MCP server.

Provides easy installation of Aleph into various MCP clients:
- Claude Desktop (macOS/Windows)
- Cursor (global/project)
- Windsurf
- Claude Code
- VSCode
- Codex CLI
- Gemini CLI

Usage:
    aleph-rlm install           # Interactive mode, detects all clients
    aleph-rlm install claude-desktop
    aleph-rlm install cursor
    aleph-rlm install windsurf
    aleph-rlm install claude-code
    aleph-rlm install codex
    aleph-rlm install gemini
    aleph-rlm install --all     # Configure all detected clients
    aleph-rlm uninstall <client>
    aleph-rlm doctor            # Verify installation
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

__all__ = ["main"]

# Try to import rich for colored output, fall back to plain text
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    RICH_AVAILABLE = True
    console: Console | None = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


# =============================================================================
# Output helpers (with/without rich)
# =============================================================================

def print_success(msg: str) -> None:
    """Print success message in green."""
    if console is not None:
        console.print(f"[green]{msg}[/green]")
    else:
        print(f"SUCCESS: {msg}")


def print_error(msg: str) -> None:
    """Print error message in red."""
    if console is not None:
        console.print(f"[red]{msg}[/red]")
    else:
        print(f"ERROR: {msg}", file=sys.stderr)


def print_warning(msg: str) -> None:
    """Print warning message in yellow."""
    if console is not None:
        console.print(f"[yellow]{msg}[/yellow]")
    else:
        print(f"WARNING: {msg}")


def print_info(msg: str) -> None:
    """Print info message in blue."""
    if console is not None:
        console.print(f"[blue]{msg}[/blue]")
    else:
        print(msg)


def print_header(title: str) -> None:
    """Print a header/title."""
    if console is not None and RICH_AVAILABLE:
        console.print(Panel(title, style="bold cyan"))
    else:
        print(f"\n{'=' * 50}")
        print(f"  {title}")
        print(f"{'=' * 50}\n")


def print_table(title: str, rows: list[tuple[str, str, str]]) -> None:
    """Print a table with Client, Status, Path columns."""
    if console is not None and RICH_AVAILABLE:
        table = Table(title=title)
        table.add_column("Client", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Path")
        for row in rows:
            table.add_row(*row)
        console.print(table)
    else:
        print(f"\n{title}")
        print("-" * 70)
        print(f"{'Client':<20} {'Status':<15} {'Path'}")
        print("-" * 70)
        for client, status, path in rows:
            print(f"{client:<20} {status:<15} {path}")
        print()


# =============================================================================
# Client configuration
# =============================================================================

@dataclass
class ClientConfig:
    """Configuration for an MCP client."""
    name: str
    display_name: str
    config_path: Callable[[], Path | None]
    is_cli: bool = False  # True for Claude Code which uses CLI commands
    restart_instruction: str = ""
    config_format: str = "json"

    def get_path(self) -> Path | None:
        """Get the config path, returns None if not applicable."""
        return self.config_path()


def _get_claude_desktop_path() -> Path | None:
    """Get Claude Desktop config path based on platform."""
    system = platform.system()
    if system == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
    elif system == "Linux":
        # XDG-compliant path
        config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        return Path(config_home) / "Claude" / "claude_desktop_config.json"
    return None


def _get_cursor_global_path() -> Path | None:
    """Get Cursor global config path."""
    return Path.home() / ".cursor" / "mcp.json"


def _get_cursor_project_path() -> Path | None:
    """Get Cursor project-level config path (current directory)."""
    return Path.cwd() / ".cursor" / "mcp.json"


def _get_windsurf_path() -> Path | None:
    """Get Windsurf config path."""
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def _get_vscode_path() -> Path | None:
    """Get VSCode project-level config path."""
    return Path.cwd() / ".vscode" / "mcp.json"


def _get_claude_code_path() -> Path | None:
    """Claude Code uses CLI, not a config file."""
    return None


def _get_codex_path() -> Path | None:
    """Get Codex CLI config path."""
    return Path.home() / ".codex" / "config.toml"

def _get_gemini_path() -> Path | None:
    """Get Gemini CLI config path."""
    return Path.home() / ".gemini" / "mcp.json"


# Define all supported clients
CLIENTS: dict[str, ClientConfig] = {
    "claude-desktop": ClientConfig(
        name="claude-desktop",
        display_name="Claude Desktop",
        config_path=_get_claude_desktop_path,
        restart_instruction="Restart Claude Desktop to load Aleph",
    ),
    "cursor": ClientConfig(
        name="cursor",
        display_name="Cursor (Global)",
        config_path=_get_cursor_global_path,
        restart_instruction="Restart Cursor to load Aleph",
    ),
    "cursor-project": ClientConfig(
        name="cursor-project",
        display_name="Cursor (Project)",
        config_path=_get_cursor_project_path,
        restart_instruction="Restart Cursor to load Aleph",
    ),
    "windsurf": ClientConfig(
        name="windsurf",
        display_name="Windsurf",
        config_path=_get_windsurf_path,
        restart_instruction="Restart Windsurf to load Aleph",
    ),
    "vscode": ClientConfig(
        name="vscode",
        display_name="VSCode (Project)",
        config_path=_get_vscode_path,
        restart_instruction="Restart VSCode to load Aleph",
    ),
    "claude-code": ClientConfig(
        name="claude-code",
        display_name="Claude Code",
        config_path=_get_claude_code_path,
        is_cli=True,
        restart_instruction="Run 'claude' to use Aleph",
    ),
    "codex": ClientConfig(
        name="codex",
        display_name="Codex CLI",
        config_path=_get_codex_path,
        restart_instruction="Restart Codex CLI to load Aleph",
        config_format="toml",
    ),
    "gemini": ClientConfig(
        name="gemini",
        display_name="Gemini CLI",
        config_path=_get_gemini_path,
        restart_instruction="Restart Gemini CLI to load Aleph",
    ),
}

# The JSON configuration to inject
ALEPH_MCP_CONFIG = {
    "command": "aleph",
    "args": ["--enable-actions", "--workspace-mode", "any", "--tool-docs", "concise"],
}


# =============================================================================
# Detection and installation logic
# =============================================================================

def _find_claude_cli() -> str | None:
    """Find the Claude Code CLI executable.

    On Windows with NPM installation, the executable may be claude.cmd or claude.ps1.
    Returns the executable name if found, None otherwise.
    """
    # Try standard 'claude' first (works on macOS/Linux and some Windows setups)
    if shutil.which("claude"):
        return "claude"

    # On Windows, NPM creates .cmd and .ps1 wrapper scripts
    if platform.system() == "Windows":
        for ext in (".cmd", ".ps1", ".exe"):
            exe_name = f"claude{ext}"
            if shutil.which(exe_name):
                return exe_name

        # Also check common npm global bin locations on Windows
        npm_paths = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            npm_paths.append(Path(appdata) / "npm" / "claude.cmd")
            npm_paths.append(Path(appdata) / "npm" / "claude.ps1")

        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            npm_paths.append(Path(localappdata) / "npm" / "claude.cmd")
            npm_paths.append(Path(localappdata) / "npm" / "claude.ps1")

        for npm_path in npm_paths:
            if npm_path.exists():
                return str(npm_path)

    return None


def is_client_installed(client: ClientConfig) -> bool:
    """Check if a client appears to be installed."""
    if client.is_cli:
        # Check if claude CLI is available
        return _find_claude_cli() is not None

    path = client.get_path()
    if path is None:
        return False

    # Check if the config directory exists (client is likely installed)
    # For Claude Desktop, check the parent directory
    if client.name == "claude-desktop":
        return path.parent.exists()

    # For editors, we check if the global config dir exists
    if client.name == "cursor":
        return path.parent.exists()

    if client.name == "windsurf":
        return path.parent.exists()

    if client.name == "codex":
        return path.parent.exists()

    if client.name == "gemini":
        return path.parent.exists()

    # For project-level configs, always return True (user may want to create)
    return True


def is_aleph_configured(client: ClientConfig) -> bool:
    """Check if Aleph is already configured in a client."""
    if client.is_cli:
        # Check claude mcp list
        claude_exe = _find_claude_cli()
        if not claude_exe:
            return False
        try:
            result = subprocess.run(
                [claude_exe, "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=claude_exe.endswith((".cmd", ".ps1")),  # Use shell for Windows scripts
            )
            return "aleph" in result.stdout.lower()
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            return False

    if client.config_format == "toml":
        return is_aleph_configured_toml(client)

    path = client.get_path()
    if path is None or not path.exists():
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return "aleph" in config.get("mcpServers", {})
    except (json.JSONDecodeError, OSError):
        return False


def backup_config(path: Path) -> Path | None:
    """Create a backup of the config file."""
    if not path.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".backup_{timestamp}.json")

    try:
        shutil.copy2(path, backup_path)
        return backup_path
    except OSError as e:
        print_warning(f"Could not create backup: {e}")
        return None


def backup_config_toml(path: Path) -> Path | None:
    """Create a backup of a TOML config file."""
    if not path.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f"{path.suffix}.backup_{timestamp}")

    try:
        shutil.copy2(path, backup_path)
        return backup_path
    except OSError as e:
        print_warning(f"Could not create backup: {e}")
        return None


def validate_json(path: Path) -> bool:
    """Validate that a JSON file is well-formed."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
        return True
    except (json.JSONDecodeError, OSError):
        return False


def validate_toml(path: Path) -> bool:
    """Validate that a TOML file is well-formed when tomllib/tomli is available."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return True
    try:
        with open(path, "rb") as f:
            tomllib.load(f)
        return True
    except (OSError, tomllib.TOMLDecodeError):
        return False


def _toml_section_exists(config_text: str, section: str) -> bool:
    pattern = rf"^\[{re.escape(section)}\]\s*$"
    return re.search(pattern, config_text, flags=re.MULTILINE) is not None


def _remove_toml_section(config_text: str, section: str) -> str:
    pattern = rf"(?ms)^\[{re.escape(section)}\]\n(?:.*\n)*?(?=^\[|\Z)"
    return re.sub(pattern, "", config_text)


def is_aleph_configured_toml(client: ClientConfig) -> bool:
    """Check if Aleph is configured in a TOML config file (Codex)."""
    path = client.get_path()
    if path is None or not path.exists():
        return False
    try:
        config_text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _toml_section_exists(config_text, "mcp_servers.aleph")


def install_to_toml_config(
    client: ClientConfig,
    dry_run: bool = False,
) -> bool:
    """Install Aleph to a TOML config file (Codex)."""
    path = client.get_path()
    if path is None:
        print_error(f"Could not determine config path for {client.display_name}")
        return False

    if path.exists():
        try:
            config_text = path.read_text(encoding="utf-8")
        except OSError as e:
            print_error(f"Could not read {path}: {e}")
            return False
    else:
        config_text = ""

    if _toml_section_exists(config_text, "mcp_servers.aleph"):
        print_warning(f"Aleph is already configured in {client.display_name}")
        return True

    block = (
        "[mcp_servers.aleph]\n"
        "command = \"aleph\"\n"
        "args = [\"--enable-actions\", \"--workspace-mode\", \"any\", \"--tool-docs\", \"concise\"]\n"
    )

    if dry_run:
        print_info(f"[DRY RUN] Would write to: {path}")
        print_info(f"[DRY RUN] Would append:\n{block}")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        backup = backup_config_toml(path)
        if backup:
            print_info(f"Backed up existing config to: {backup}")

    new_text = config_text
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    if new_text and not new_text.endswith("\n\n"):
        new_text += "\n"
    new_text += block

    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        print_error(f"Could not write to {path}: {e}")
        return False

    if not validate_toml(path):
        print_error(f"Written TOML may be invalid! Check {path}")
        return False

    print_success(f"Configured Aleph in {client.display_name}")
    print_info(f"Config file: {path}")
    if client.restart_instruction:
        print_info(client.restart_instruction)

    return True


def uninstall_from_toml_config(
    client: ClientConfig,
    dry_run: bool = False,
) -> bool:
    """Remove Aleph from a TOML config file (Codex)."""
    path = client.get_path()
    if path is None:
        print_error(f"Could not determine config path for {client.display_name}")
        return False

    if not path.exists():
        print_warning(f"Config file does not exist: {path}")
        return True

    try:
        config_text = path.read_text(encoding="utf-8")
    except OSError as e:
        print_error(f"Could not read {path}: {e}")
        return False

    has_main = _toml_section_exists(config_text, "mcp_servers.aleph")
    has_env = _toml_section_exists(config_text, "mcp_servers.aleph.env")
    if not has_main and not has_env:
        print_warning(f"Aleph is not configured in {client.display_name}")
        return True

    if dry_run:
        print_info(f"[DRY RUN] Would remove Aleph from: {path}")
        return True

    backup = backup_config_toml(path)
    if backup:
        print_info(f"Backed up existing config to: {backup}")

    new_text = _remove_toml_section(config_text, "mcp_servers.aleph.env")
    new_text = _remove_toml_section(new_text, "mcp_servers.aleph")
    new_text = re.sub(r"\n{3,}", "\n\n", new_text).rstrip()
    if new_text:
        new_text += "\n"

    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        print_error(f"Could not write to {path}: {e}")
        return False

    print_success(f"Removed Aleph from {client.display_name}")
    return True


def install_to_config_file(
    client: ClientConfig,
    dry_run: bool = False,
) -> bool:
    """Install Aleph to a JSON config file."""
    path = client.get_path()
    if path is None:
        print_error(f"Could not determine config path for {client.display_name}")
        return False

    # Load existing config or create new
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            print_error(f"Invalid JSON in {path}: {e}")
            return False
        except OSError as e:
            print_error(f"Could not read {path}: {e}")
            return False
    else:
        config = {}

    # Ensure mcpServers exists
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Check if already configured
    if "aleph" in config["mcpServers"]:
        print_warning(f"Aleph is already configured in {client.display_name}")
        return True

    # Add Aleph config
    config["mcpServers"]["aleph"] = ALEPH_MCP_CONFIG.copy()

    if dry_run:
        print_info(f"[DRY RUN] Would write to: {path}")
        print_info(f"[DRY RUN] New config:\n{json.dumps(config, indent=2)}")
        return True

    # Create parent directory if needed
    path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing config
    if path.exists():
        backup = backup_config(path)
        if backup:
            print_info(f"Backed up existing config to: {backup}")

    # Write new config
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError as e:
        print_error(f"Could not write to {path}: {e}")
        return False

    # Validate
    if not validate_json(path):
        print_error(f"Written JSON is invalid! Check {path}")
        return False

    print_success(f"Configured Aleph in {client.display_name}")
    print_info(f"Config file: {path}")
    if client.restart_instruction:
        print_info(client.restart_instruction)

    return True


def install_to_claude_code(dry_run: bool = False) -> bool:
    """Install Aleph to Claude Code using CLI."""
    claude_exe = _find_claude_cli()
    if not claude_exe:
        print_error("Claude Code CLI not found. Install it first: https://claude.ai/code")
        if platform.system() == "Windows":
            print_info("If installed via NPM, ensure %APPDATA%\\npm is in your PATH")
        return False

    if dry_run:
        print_info(
            f"[DRY RUN] Would run: {claude_exe} mcp add aleph aleph -- --enable-actions --workspace-mode any --tool-docs concise"
        )
        return True

    try:
        result = subprocess.run(
            [
                claude_exe,
                "mcp",
                "add",
                "aleph",
                "aleph",
                "--",
                "--enable-actions",
                "--workspace-mode",
                "any",
                "--tool-docs",
                "concise",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            shell=claude_exe.endswith((".cmd", ".ps1")),  # Use shell for Windows scripts
        )

        if result.returncode == 0:
            print_success("Configured Aleph in Claude Code")
            print_info("Run 'claude' to use Aleph")
            return True
        else:
            # Check if it's already installed
            if "already exists" in result.stderr.lower():
                print_warning("Aleph is already configured in Claude Code")
                return True
            print_error(f"Failed to add Aleph to Claude Code: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print_error("Command timed out")
        return False
    except FileNotFoundError:
        print_error("Claude Code CLI not found")
        return False


def uninstall_from_config_file(
    client: ClientConfig,
    dry_run: bool = False,
) -> bool:
    """Remove Aleph from a JSON config file."""
    path = client.get_path()
    if path is None:
        print_error(f"Could not determine config path for {client.display_name}")
        return False

    if not path.exists():
        print_warning(f"Config file does not exist: {path}")
        return True

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print_error(f"Could not read {path}: {e}")
        return False

    if "mcpServers" not in config or "aleph" not in config["mcpServers"]:
        print_warning(f"Aleph is not configured in {client.display_name}")
        return True

    if dry_run:
        print_info(f"[DRY RUN] Would remove 'aleph' from mcpServers in: {path}")
        return True

    # Backup before removing
    backup = backup_config(path)
    if backup:
        print_info(f"Backed up existing config to: {backup}")

    # Remove Aleph
    del config["mcpServers"]["aleph"]

    # Clean up empty mcpServers
    if not config["mcpServers"]:
        del config["mcpServers"]

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError as e:
        print_error(f"Could not write to {path}: {e}")
        return False

    print_success(f"Removed Aleph from {client.display_name}")
    return True


def uninstall_from_claude_code(dry_run: bool = False) -> bool:
    """Remove Aleph from Claude Code using CLI."""
    claude_exe = _find_claude_cli()
    if not claude_exe:
        print_error("Claude Code CLI not found")
        return False

    if dry_run:
        print_info(f"[DRY RUN] Would run: {claude_exe} mcp remove aleph")
        return True

    try:
        result = subprocess.run(
            [claude_exe, "mcp", "remove", "aleph"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=claude_exe.endswith((".cmd", ".ps1")),  # Use shell for Windows scripts
        )

        if result.returncode == 0:
            print_success("Removed Aleph from Claude Code")
            return True
        else:
            if "not found" in result.stderr.lower():
                print_warning("Aleph is not configured in Claude Code")
                return True
            print_error(f"Failed to remove Aleph from Claude Code: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print_error("Command timed out")
        return False
    except FileNotFoundError:
        print_error("Claude Code CLI not found")
        return False


def install_client(client: ClientConfig, dry_run: bool = False) -> bool:
    """Install Aleph to a specific client."""
    if client.is_cli:
        return install_to_claude_code(dry_run)
    if client.config_format == "toml":
        return install_to_toml_config(client, dry_run)
    return install_to_config_file(client, dry_run)


def uninstall_client(client: ClientConfig, dry_run: bool = False) -> bool:
    """Uninstall Aleph from a specific client."""
    if client.is_cli:
        return uninstall_from_claude_code(dry_run)
    if client.config_format == "toml":
        return uninstall_from_toml_config(client, dry_run)
    return uninstall_from_config_file(client, dry_run)


# =============================================================================
# Doctor command
# =============================================================================

def doctor() -> bool:
    """Verify Aleph installation and diagnose issues."""
    print_header("Aleph Doctor")

    all_ok = True

    # Check if aleph is available
    print_info("Checking aleph command...")
    if shutil.which("aleph"):
        print_success("aleph is in PATH")
    else:
        print_error("aleph not found in PATH")
        print_info("Try reinstalling: pip install \"aleph-rlm[mcp]\"")
        all_ok = False

    # Check MCP dependency
    print_info("\nChecking MCP dependency...")
    try:
        import mcp  # noqa: F401
        print_success("MCP package is installed")
    except ImportError:
        print_error("MCP package not installed")
        print_info("Install with: pip install \"aleph-rlm[mcp]\"")
        all_ok = False

    # Check each client
    print_info("\nChecking client configurations...")
    rows = []

    for name, client in CLIENTS.items():
        if client.is_cli:
            claude_exe = _find_claude_cli()
            if claude_exe:
                if is_aleph_configured(client):
                    status = "Configured"
                else:
                    status = "Not configured"
                path_str = f"(CLI: {claude_exe})"
            else:
                status = "Not installed"
                path_str = "-"
        else:
            path = client.get_path()
            if path is None:
                status = "N/A"
                path_str = "-"
            elif not is_client_installed(client):
                status = "Not installed"
                path_str = str(path)
            elif is_aleph_configured(client):
                status = "Configured"
                path_str = str(path)
            else:
                status = "Not configured"
                path_str = str(path)

        rows.append((client.display_name, status, path_str))

    print_table("MCP Client Status", rows)

    # Test MCP server startup
    print_info("Testing MCP server startup...")
    try:
        from aleph.mcp.local_server import AlephMCPServerLocal  # noqa: F401
        print_success("Aleph MCP server module loads correctly")
    except ImportError as e:
        print_error(f"Failed to import MCP server: {e}")
        all_ok = False
    except RuntimeError as e:
        if "mcp" in str(e).lower():
            print_error(f"MCP dependency issue: {e}")
            print_info("Install with: pip install \"aleph-rlm[mcp]\"")
        else:
            print_error(f"Server error: {e}")
        all_ok = False

    print()
    if all_ok:
        print_success("All checks passed!")
    else:
        print_error("Some checks failed. See above for details.")

    return all_ok


# =============================================================================
# Interactive mode
# =============================================================================

def interactive_install(dry_run: bool = False) -> None:
    """Interactive installation mode."""
    print_header("Aleph MCP Server Installer")

    # Detect installed clients
    detected = []
    for name, client in CLIENTS.items():
        if is_client_installed(client):
            configured = is_aleph_configured(client)
            detected.append((name, client, configured))

    if not detected:
        print_warning("No MCP clients detected!")
        print_info("Supported clients: Claude Desktop, Cursor, Windsurf, VSCode, Claude Code, Codex CLI")
        return

    print_info(f"Detected {len(detected)} MCP client(s):\n")

    rows = []
    for name, client, configured in detected:
        status = "Already configured" if configured else "Not configured"
        path = client.get_path()
        path_str = "(CLI)" if client.is_cli else str(path) if path else "-"
        rows.append((client.display_name, status, path_str))

    print_table("Detected Clients", rows)

    # Ask user which to configure
    to_configure = []
    for name, client, configured in detected:
        if configured:
            print_info(f"{client.display_name}: Already configured, skipping")
            continue

        try:
            response = input(f"Configure {client.display_name}? [Y/n]: ").strip().lower()
            if response in ("", "y", "yes"):
                to_configure.append(client)
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return

    if not to_configure:
        print_info("No clients to configure.")
        return

    print()
    for client in to_configure:
        install_client(client, dry_run)
        print()


def install_all(dry_run: bool = False) -> None:
    """Install Aleph to all detected clients."""
    print_header("Installing Aleph to All Detected Clients")

    for name, client in CLIENTS.items():
        if not is_client_installed(client):
            print_info(f"Skipping {client.display_name} (not installed)")
            continue

        if is_aleph_configured(client):
            print_info(f"Skipping {client.display_name} (already configured)")
            continue

        install_client(client, dry_run)
        print()


# =============================================================================
# CLI entry point
# =============================================================================

def print_usage() -> None:
    """Print CLI usage information."""
    print("""
Aleph MCP Server Installer

Usage:
    aleph-rlm install              Interactive mode - detect and configure clients
    aleph-rlm install <client>     Configure a specific client
    aleph-rlm install --all        Configure all detected clients
    aleph-rlm uninstall <client>   Remove Aleph from a client
    aleph-rlm doctor               Verify installation

Clients:
    claude-desktop     Claude Desktop app
    cursor             Cursor editor (global config)
    cursor-project     Cursor editor (project config)
    windsurf           Windsurf editor
    vscode             VSCode (project config)
    claude-code        Claude Code CLI
    codex              Codex CLI
    gemini             Gemini CLI

Options:
    --dry-run          Preview changes without writing
    --help, -h         Show this help message

Examples:
    aleph-rlm install                     # Interactive mode
    aleph-rlm install claude-desktop      # Configure Claude Desktop
    aleph-rlm install codex               # Configure Codex CLI
    aleph-rlm install --all --dry-run     # Preview all installations
    aleph-rlm uninstall cursor            # Remove from Cursor
    aleph-rlm doctor                      # Check installation status
""")


def main() -> None:
    """CLI entry point."""
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h", "help"):
        print_usage()
        return

    dry_run = "--dry-run" in args
    if dry_run:
        args = [a for a in args if a != "--dry-run"]

    command = args[0] if args else ""

    if command == "doctor":
        success = doctor()
        sys.exit(0 if success else 1)

    elif command == "install":
        if len(args) == 1:
            # Interactive mode
            interactive_install(dry_run)
        elif args[1] == "--all":
            install_all(dry_run)
        elif args[1] in CLIENTS:
            client = CLIENTS[args[1]]
            success = install_client(client, dry_run)
            sys.exit(0 if success else 1)
        else:
            print_error(f"Unknown client: {args[1]}")
            print_info(f"Available clients: {', '.join(CLIENTS.keys())}")
            sys.exit(1)

    elif command == "uninstall":
        if len(args) < 2:
            print_error("Please specify a client to uninstall from")
            print_info(f"Available clients: {', '.join(CLIENTS.keys())}")
            sys.exit(1)

        client_name = args[1]
        if client_name not in CLIENTS:
            print_error(f"Unknown client: {client_name}")
            print_info(f"Available clients: {', '.join(CLIENTS.keys())}")
            sys.exit(1)

        client = CLIENTS[client_name]
        success = uninstall_client(client, dry_run)
        sys.exit(0 if success else 1)

    else:
        print_error(f"Unknown command: {command}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()

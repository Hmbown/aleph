#!/usr/bin/env python3
"""Sync version strings to match pyproject.toml."""

from __future__ import annotations

import argparse
import pathlib
import re


def _load_toml(path: pathlib.Path) -> dict:
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover - fallback for 3.10
        try:
            import tomli as tomllib
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("tomli is required on Python < 3.11") from exc
    return tomllib.loads(path.read_text())


def _sync_file(path: pathlib.Path, new_text: str, check: bool) -> bool:
    old_text = path.read_text()
    if old_text == new_text:
        return False
    if check:
        raise SystemExit(f"{path} is out of date with pyproject.toml")
    path.write_text(new_text)
    return True


def _sync_init(version: str, check: bool) -> bool:
    path = pathlib.Path("aleph/__init__.py")
    text = path.read_text()
    new_text, count = re.subn(
        r'(__version__\s*=\s*["\'])([^"\']+)(["\'])',
        rf"\g<1>{version}\g<3>",
        text,
        count=1,
    )
    if count == 0:
        raise SystemExit("Could not find __version__ in aleph/__init__.py")
    return _sync_file(path, new_text, check)


def _sync_web(version: str, check: bool) -> bool:
    path = pathlib.Path("web/index.html")
    text = path.read_text()
    new_text, count = re.subn(
        r'(<div class="version-badge">)[^<]+(</div>)',
        f"\\g<1>v{version}\\2",
        text,
        count=1,
    )
    if count == 0:
        raise SystemExit("Could not find version badge in web/index.html")
    return _sync_file(path, new_text, check)


def _sync_manifest(path_str: str, version: str, check: bool) -> bool:
    path = pathlib.Path(path_str)
    text = path.read_text()
    new_text, count = re.subn(
        r'("version"\s*:\s*")([^"]+)(")',
        rf'\g<1>{version}\g<3>',
        text,
        count=1,
    )
    if count == 0:
        raise SystemExit(f"Could not find version in {path_str}")
    return _sync_file(path, new_text, check)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync version strings.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any file is out of date.",
    )
    args = parser.parse_args()

    pyproject = _load_toml(pathlib.Path("pyproject.toml"))
    version = pyproject["project"]["version"]

    changed = False
    changed |= _sync_init(version, args.check)
    changed |= _sync_web(version, args.check)
    changed |= _sync_manifest("plugins/.claude-plugin/marketplace.json", version, args.check)
    changed |= _sync_manifest("plugins/aleph/.claude-plugin/plugin.json", version, args.check)
    changed |= _sync_manifest("plugins/aleph/.codex-plugin/plugin.json", version, args.check)

    if not args.check and changed:
        print(f"Updated version strings to {version}")
    elif args.check:
        print(f"Version strings are up to date: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

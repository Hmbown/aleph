"""Tests for workspace root detection and env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from aleph.mcp.local_server import _detect_workspace_root


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    sub = repo / "sub"
    sub.mkdir()
    return repo, sub


def test_detect_workspace_root_relative_override_uses_pwd(monkeypatch, tmp_path: Path) -> None:
    repo, sub = _make_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.chdir(other)
    monkeypatch.setenv("ALEPH_WORKSPACE_ROOT", ".")
    monkeypatch.setenv("PWD", str(sub))
    monkeypatch.delenv("INIT_CWD", raising=False)

    assert _detect_workspace_root() == repo


def test_detect_workspace_root_relative_override_uses_init_cwd(monkeypatch, tmp_path: Path) -> None:
    repo, sub = _make_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.chdir(other)
    monkeypatch.setenv("ALEPH_WORKSPACE_ROOT", ".")
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setenv("INIT_CWD", str(sub))

    assert _detect_workspace_root() == repo


def test_detect_workspace_root_expands_tilde(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo = home / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "sub").mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALEPH_WORKSPACE_ROOT", "~/repo/sub")
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)

    assert _detect_workspace_root() == repo

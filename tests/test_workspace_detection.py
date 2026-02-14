"""Tests for workspace root detection and env overrides."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aleph.mcp.local_server import _detect_workspace_root
from aleph.mcp.workspace import roots_to_workspace_root


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    sub = repo / "sub"
    sub.mkdir()
    return repo, sub


def test_detect_workspace_root_uses_env_override(monkeypatch, tmp_path: Path) -> None:
    """ALEPH_WORKSPACE_ROOT takes precedence over everything."""
    repo, sub = _make_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.chdir(other)
    monkeypatch.setenv("ALEPH_WORKSPACE_ROOT", str(repo))
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)

    assert _detect_workspace_root() == repo


def test_detect_workspace_root_uses_pwd(monkeypatch, tmp_path: Path) -> None:
    """Without ALEPH_WORKSPACE_ROOT, uses PWD and finds git root."""
    repo, sub = _make_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.chdir(other)
    monkeypatch.delenv("ALEPH_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("PWD", str(sub))
    monkeypatch.delenv("INIT_CWD", raising=False)

    assert _detect_workspace_root() == repo


def test_detect_workspace_root_uses_init_cwd(monkeypatch, tmp_path: Path) -> None:
    """Without PWD, falls back to INIT_CWD and finds git root."""
    repo, sub = _make_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.chdir(other)
    monkeypatch.delenv("ALEPH_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setenv("INIT_CWD", str(sub))

    assert _detect_workspace_root() == repo


def test_detect_workspace_root_expands_tilde(monkeypatch, tmp_path: Path) -> None:
    """Tilde expansion works in ALEPH_WORKSPACE_ROOT."""
    home = tmp_path / "home"
    home.mkdir()
    workspace = home / "myworkspace"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALEPH_WORKSPACE_ROOT", "~/myworkspace")
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)

    assert _detect_workspace_root() == workspace


def test_detect_workspace_root_falls_back_to_cwd(monkeypatch, tmp_path: Path) -> None:
    """Without any env vars, uses cwd and finds git root."""
    repo, sub = _make_repo(tmp_path)

    monkeypatch.chdir(sub)
    monkeypatch.delenv("ALEPH_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)

    assert _detect_workspace_root() == repo


# ---------------------------------------------------------------------------
# roots_to_workspace_root tests
# ---------------------------------------------------------------------------

def _root(uri: str) -> SimpleNamespace:
    """Create a mock MCP Root object."""
    return SimpleNamespace(uri=uri, name=None)


def test_roots_to_workspace_root_picks_git_root(tmp_path: Path) -> None:
    """Prefer a root that contains a .git directory."""
    plain = tmp_path / "plain"
    plain.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    roots = [_root(plain.as_uri()), _root(repo.as_uri())]
    result = roots_to_workspace_root(roots)
    assert result == repo


def test_roots_to_workspace_root_falls_back_to_first(tmp_path: Path) -> None:
    """Without git, picks the first valid directory."""
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()

    roots = [_root(a.as_uri()), _root(b.as_uri())]
    result = roots_to_workspace_root(roots)
    assert result == a


def test_roots_to_workspace_root_empty_list() -> None:
    """Empty roots list returns None."""
    assert roots_to_workspace_root([]) is None


def test_roots_to_workspace_root_ignores_non_file_uris(tmp_path: Path) -> None:
    """Non-file:// URIs are skipped."""
    d = tmp_path / "d"
    d.mkdir()
    roots = [_root("https://example.com"), _root(d.as_uri())]
    result = roots_to_workspace_root(roots)
    assert result == d


def test_roots_to_workspace_root_ignores_missing_dirs(tmp_path: Path) -> None:
    """Non-existent directories are skipped."""
    missing = tmp_path / "does_not_exist"
    real = tmp_path / "real"
    real.mkdir()
    roots = [_root(missing.as_uri()), _root(real.as_uri())]
    result = roots_to_workspace_root(roots)
    assert result == real


def test_roots_to_workspace_root_skips_bad_attrs() -> None:
    """Objects without a string uri attribute are ignored."""
    bad1 = SimpleNamespace(uri=123)
    bad2 = SimpleNamespace()
    assert roots_to_workspace_root([bad1, bad2]) is None

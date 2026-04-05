from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aleph.mcp.local_server import ActionConfig, AlephMCPServerLocal
from aleph.repl.sandbox import SandboxConfig


async def _call_tool(server: AlephMCPServerLocal, tool_name: str, **kwargs: Any) -> Any:
    _, payload = await server.server.call_tool(tool_name, kwargs)
    return payload["result"]


@pytest.mark.asyncio
async def test_workspace_and_status_object_json_contracts(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("print('hi')\n", encoding="utf-8")

    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )

    manifest_obj = await _call_tool(
        server,
        "load_workspace_manifest",
        context_id="repo",
        output="object",
        confirm=True,
    )
    manifest_json = await _call_tool(
        server,
        "load_workspace_manifest",
        context_id="repo-json",
        output="json",
        confirm=True,
    )
    assert set(manifest_obj.keys()) == {
        "status",
        "context_id",
        "workspace_root",
        "roots",
        "file_count",
        "truncated",
        "binding",
        "note",
        "size_chars",
        "size_lines",
    }
    assert set(json.loads(manifest_json).keys()) == set(manifest_obj.keys())

    status_obj = await _call_tool(server, "get_status", context_id="repo", output="object")
    status_json = await _call_tool(server, "get_status", context_id="repo", output="json")
    expected_status_keys = {
        "context_id",
        "iterations",
        "evidence_count",
        "tasks_count",
        "variables",
        "size_chars",
        "size_lines",
        "workspace_root",
        "workspace_root_source",
        "context_policy",
        "action_policy",
        "auto_memory_pack",
        "workspace_binding",
        "workspace_binding_summary",
        "workspace_binding_status",
    }
    assert set(status_obj.keys()) == expected_status_keys
    assert set(json.loads(status_json).keys()) == expected_status_keys

    list_obj = await _call_tool(server, "list_contexts", output="object")
    list_json = await _call_tool(server, "list_contexts", output="json")
    assert set(list_obj.keys()) == {"count", "items"}
    assert set(json.loads(list_json).keys()) == {"count", "items"}
    assert list_obj["count"] >= 2
    assert set(list_obj["items"][0].keys()) == {
        "id",
        "chars",
        "lines",
        "iterations",
        "evidence",
        "workspace_binding",
        "workspace_binding_summary",
    }


@pytest.mark.asyncio
async def test_refresh_context_object_contract(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )
    await _call_tool(
        server,
        "load_file",
        path="notes.txt",
        context_id="notes",
        confirm=True,
    )

    file_path.write_text("beta\n", encoding="utf-8")
    refresh_obj = await _call_tool(
        server,
        "refresh_context",
        context_id="notes",
        output="object",
        confirm=True,
    )
    refresh_json = await _call_tool(
        server,
        "refresh_context",
        context_id="notes",
        output="json",
        confirm=True,
    )
    expected_keys = {"status", "context_id", "binding", "size_chars", "size_lines", "note"}
    assert set(refresh_obj.keys()) == expected_keys
    assert set(json.loads(refresh_json).keys()) == expected_keys

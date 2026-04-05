from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aleph.mcp.local_server import ActionConfig, AlephMCPServerLocal
from aleph.repl.sandbox import SandboxConfig


async def _call_tool(server: AlephMCPServerLocal, tool_name: str, **kwargs: Any) -> Any:
    _, payload = await server.server.call_tool(tool_name, kwargs)
    return payload["result"]


@pytest.mark.asyncio
async def test_workspace_manifest_contract_and_refresh(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Repo\n", encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('alpha')\n", encoding="utf-8")

    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )

    manifest = await _call_tool(
        server,
        "load_workspace_manifest",
        context_id="workspace",
        output="object",
        confirm=True,
    )
    assert manifest["status"] == "success"
    assert manifest["binding"]["kind"] == "manifest"
    initial_file_count = manifest["binding"]["file_count"]
    assert initial_file_count >= 2

    status = await _call_tool(server, "get_status", context_id="workspace", output="object")
    assert status["action_policy"] == "read-write"
    assert status["workspace_binding"]["kind"] == "manifest"
    assert status["workspace_binding_status"]["kind"] == "manifest"

    listed = await _call_tool(server, "list_contexts", output="object")
    item = next(entry for entry in listed["items"] if entry["id"] == "workspace")
    assert item["workspace_binding_summary"].startswith("manifest:")

    (src_dir / "extra.py").write_text("print('beta')\n", encoding="utf-8")
    refreshed = await _call_tool(
        server,
        "refresh_context",
        context_id="workspace",
        output="object",
        confirm=True,
    )
    assert refreshed["status"] == "success"
    assert refreshed["binding"]["kind"] == "manifest"
    assert refreshed["binding"]["file_count"] == initial_file_count + 1


@pytest.mark.asyncio
async def test_load_file_creates_refreshable_workspace_binding(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("alpha\n", encoding="utf-8")

    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )

    result = await _call_tool(
        server,
        "load_file",
        path="notes.txt",
        context_id="notes",
        confirm=True,
    )
    assert "Context loaded 'notes'" in result

    status = await _call_tool(server, "get_status", context_id="notes", output="object")
    assert status["workspace_binding"]["kind"] == "file"
    assert status["workspace_binding_summary"] == "file:notes.txt"
    assert status["workspace_binding_status"]["exists"] is True
    assert status["workspace_binding_status"]["stale"] is False

    file_path.write_text("beta\n", encoding="utf-8")
    refreshed = await _call_tool(
        server,
        "refresh_context",
        context_id="notes",
        output="object",
        confirm=True,
    )
    assert refreshed["status"] == "success"
    assert server._sessions["notes"].repl.get_variable("ctx") == "beta\n"


@pytest.mark.asyncio
async def test_refresh_context_preserves_reasoning_and_task_state(tmp_path: Path) -> None:
    file_path = tmp_path / "story.txt"
    file_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )
    await _call_tool(
        server,
        "load_file",
        path="story.txt",
        context_id="story",
        confirm=True,
    )
    await _call_tool(server, "search_context", context_id="story", pattern="beta")
    await _call_tool(
        server,
        "tasks",
        context_id="story",
        action="add",
        description="check beta branch",
    )
    await _call_tool(server, "think", context_id="story", question="What changed in beta?")

    file_path.write_text("alpha\nbeta\nbeta-2\ngamma\n", encoding="utf-8")
    refreshed = await _call_tool(
        server,
        "refresh_context",
        context_id="story",
        output="object",
        confirm=True,
    )
    assert refreshed["status"] == "success"

    status = await _call_tool(server, "get_status", context_id="story", output="object")
    assert status["tasks_count"] == 1
    assert status["evidence_count"] >= 2
    assert status["iterations"] >= 4

    task_list = await _call_tool(server, "tasks", context_id="story", action="list")
    assert "check beta branch" in task_list

    assert "What changed in beta?" in server._sessions["story"].think_history


@pytest.mark.asyncio
async def test_read_only_action_policy_blocks_writes_and_commands(tmp_path: Path) -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(
            enabled=True,
            workspace_root=tmp_path,
            action_policy="read-only",
        ),
    )
    await _call_tool(server, "load_context", context="persist me", context_id="doc")

    save_result = await _call_tool(
        server,
        "save_session",
        path="pack.json",
        confirm=True,
        output="object",
    )
    assert "read-only" in save_result["error"]

    write_result = await _call_tool(
        server,
        "write_file",
        path="blocked.txt",
        content="nope",
        confirm=True,
        output="object",
    )
    assert "read-only" in write_result["error"]

    command_result = await _call_tool(
        server,
        "run_command",
        cmd="echo hi",
        confirm=True,
        output="object",
    )
    assert "read-only" in command_result["error"]

    tests_result = await _call_tool(
        server,
        "run_tests",
        confirm=True,
        output="object",
    )
    assert "read-only" in tests_result["error"]


@pytest.mark.asyncio
async def test_workspace_binding_persists_through_memory_pack(tmp_path: Path) -> None:
    file_path = tmp_path / "persisted.txt"
    file_path.write_text("v1\n", encoding="utf-8")

    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0),
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )
    await _call_tool(
        server,
        "load_file",
        path="persisted.txt",
        context_id="persisted",
        confirm=True,
    )
    await _call_tool(
        server,
        "tasks",
        context_id="persisted",
        action="add",
        description="re-open after restore",
    )

    save_result = await _call_tool(
        server,
        "save_session",
        path="workspace-pack.json",
        confirm=True,
        output="object",
    )
    assert save_result["status"] == "success"

    server._sessions.clear()
    load_result = await _call_tool(
        server,
        "load_session",
        path="workspace-pack.json",
        confirm=True,
        output="object",
    )
    assert "persisted" in load_result["loaded"]

    status = await _call_tool(server, "get_status", context_id="persisted", output="object")
    assert status["workspace_binding"]["kind"] == "file"
    assert status["workspace_binding_summary"] == "file:persisted.txt"
    assert status["tasks_count"] == 1

    task_list = await _call_tool(server, "tasks", context_id="persisted", action="list")
    assert "re-open after restore" in task_list

    file_path.write_text("v2\n", encoding="utf-8")
    refreshed = await _call_tool(
        server,
        "refresh_context",
        context_id="persisted",
        confirm=True,
        output="object",
    )
    assert refreshed["status"] == "success"
    assert server._sessions["persisted"].repl.get_variable("ctx") == "v2\n"

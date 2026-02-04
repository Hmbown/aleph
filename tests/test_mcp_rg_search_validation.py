from __future__ import annotations

from aleph.mcp.local_server import ActionConfig, AlephMCPServerLocal


def test_rg_search_accepts_paths_as_string(tmp_path) -> None:
    server = AlephMCPServerLocal(
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )
    tool = server.server._tool_manager.get_tool("rg_search")
    args_model = tool.fn_metadata.arg_model

    args = args_model.model_validate({"pattern": "dialectical_single_shot", "paths": "."})
    assert args.paths == "."

    args2 = args_model.model_validate({"pattern": "dialectical_single_shot", "paths": ["."]})
    assert args2.paths == ["."]

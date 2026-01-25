"""Tests for the alef CLI runner."""

from __future__ import annotations

import json

from aleph.types import AlephResponse, ContextCollection
import aleph.alef_cli as alef_cli


class DummyAleph:
    def __init__(self, answer: str = "ok", success: bool = True) -> None:
        self.answer = answer
        self.success = success
        self.calls: list[tuple[str, object, dict[str, object]]] = []

    def complete_sync(self, query: str, context: object, **kwargs: object) -> AlephResponse:
        self.calls.append((query, context, dict(kwargs)))
        return AlephResponse(
            answer=self.answer,
            success=self.success,
            total_iterations=1,
            max_depth_reached=0,
            total_tokens=5,
            total_cost_usd=0.0,
            wall_time_seconds=0.01,
            trajectory=[],
            error=None,
            error_type=None,
        )


def test_run_parses_json_context(monkeypatch) -> None:
    dummy = DummyAleph()
    monkeypatch.setattr(alef_cli, "create_aleph", lambda _config: dummy)

    exit_code = alef_cli.main(["run", "--context", '{"a": 1}', "hello"])

    assert exit_code == 0
    assert isinstance(dummy.calls[0][1], dict)
    assert dummy.calls[0][1]["a"] == 1


def test_run_multiple_context_files(monkeypatch, tmp_path) -> None:
    file_a = tmp_path / "a.txt"
    file_b = tmp_path / "b.json"
    file_a.write_text("alpha", encoding="utf-8")
    file_b.write_text('{"beta": 2}', encoding="utf-8")

    dummy = DummyAleph()
    monkeypatch.setattr(alef_cli, "create_aleph", lambda _config: dummy)

    exit_code = alef_cli.main(
        [
            "run",
            "--context-file",
            str(file_a),
            "--context-file",
            str(file_b),
            "hello",
        ]
    )

    assert exit_code == 0
    context = dummy.calls[0][1]
    assert isinstance(context, ContextCollection)
    assert len(context.items) == 2


def test_run_json_output(monkeypatch, capsys) -> None:
    dummy = DummyAleph(answer="hello")
    monkeypatch.setattr(alef_cli, "create_aleph", lambda _config: dummy)

    exit_code = alef_cli.main(["run", "--context", "ctx", "--json", "prompt"])

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["answer"] == "hello"
    assert payload["success"] is True

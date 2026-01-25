"""Observability utilities.

The core Aleph API always returns a full trajectory (unless disabled). This
module provides small helpers for pretty-printing and exporting it.

Optional: install `rich` to get nicer console output.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, cast

from ..types import TrajectoryStep


class TrajectoryLogger:
    """Logs Aleph trajectory steps to the standard logging system and/or a file."""

    def __init__(
        self,
        name: str = "aleph",
        level: str | int = "INFO",
        jsonl_path: str | Path | None = None,
        use_rich: bool = True,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(fmt)
            self._logger.addHandler(handler)

        self._jsonl_path = Path(jsonl_path) if jsonl_path else None
        self._use_rich = use_rich

        self._rich_console = None
        if use_rich:
            try:
                from rich.console import Console

                self._rich_console = Console()
            except Exception:
                self._rich_console = None

    def log_step(self, step: TrajectoryStep) -> None:
        msg = self._format_step(step)
        if self._rich_console is not None:
            self._rich_console.print(msg)
        else:
            self._logger.info(msg)

        if self._jsonl_path is not None:
            self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self._jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_step_to_json(step), ensure_ascii=False) + "\n")

    def _format_step(self, step: TrajectoryStep) -> str:
        act = step.action.action_type.value
        return (
            f"[{step.step_number}] depth={step.depth} act={act} "
            f"prompt_toks={step.prompt_tokens} result_toks={step.result_tokens} "
            f"cum_toks={step.cumulative_tokens} cost=${step.cumulative_cost:.4f}"
        )


def _step_to_json(step: TrajectoryStep) -> dict[str, object]:
    d = cast(dict[str, object], asdict(step))
    # datetime isn't JSON serializable by default
    d["timestamp"] = step.timestamp.isoformat()
    # Convert nested enum values (ActionType) to strings
    if "action" in d and isinstance(d["action"], dict):
        action_dict = cast(dict[str, object], d["action"])
        if "action_type" in action_dict:
            from ..types import ActionType
            val = action_dict["action_type"]
            if isinstance(val, ActionType):
                action_dict["action_type"] = val.value
    return d


def trajectory_to_json(trajectory: Iterable[TrajectoryStep]) -> list[dict[str, object]]:
    return [_step_to_json(s) for s in trajectory]

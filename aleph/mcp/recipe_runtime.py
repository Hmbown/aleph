"""Recipe execution and compilation runtime for the local MCP server.

This module contains the core recipe execution engine (step dispatch,
budget enforcement, sub-query orchestration within recipes) and the
recipe-code compilation flow (execute DSL code, extract the recipe
object, validate).
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Callable

from ..repl import helpers as repl_helpers
from .formatting import _to_jsonable
from .recipes import estimate_recipe as _estimate_recipe
from .recipes import validate_recipe as _validate_recipe
from .session import _Evidence, _coerce_context_to_text

if TYPE_CHECKING:
    from .local_server import AlephMCPServerLocal


__all__ = [
    "compile_recipe_code",
    "execute_recipe",
    "recipe_context_slice",
    "recipe_preview",
]


def recipe_preview(value: Any, limit: int = 180) -> str:
    text = _coerce_context_to_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def recipe_context_slice(value: Any, context_field: str | None) -> str:
    selected = value
    if context_field:
        if isinstance(value, dict):
            selected = value.get(context_field)
        elif isinstance(value, list):
            extracted: list[Any] = []
            for item in value:
                if isinstance(item, dict):
                    extracted.append(item.get(context_field))
                else:
                    extracted.append(item)
            selected = extracted
    return _coerce_context_to_text(selected)


async def execute_recipe(
    owner: "AlephMCPServerLocal",
    *,
    recipe: dict[str, Any],
    context_id_override: str | None = None,
    dry_run: bool = False,
    progress_callback: Callable[[float, float | None, str | None], Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    normalized, errors = _validate_recipe(recipe)
    if errors:
        return False, {"errors": errors}
    assert normalized is not None

    resolved_context_id = context_id_override or normalized["context_id"]
    if resolved_context_id not in owner._sessions:
        return False, {"error": f"No context loaded with ID '{resolved_context_id}'."}

    estimate = _estimate_recipe(normalized)
    if dry_run:
        return True, {
            "context_id": resolved_context_id,
            "mode": "dry_run",
            "recipe": normalized,
            "estimate": estimate,
        }

    session = owner._sessions[resolved_context_id]
    budget = normalized["budget"]
    max_steps = int(budget["max_steps"])
    max_sub_queries = int(budget["max_sub_queries"])

    current: Any = session.repl.get_variable("ctx")
    variables: dict[str, Any] = {"ctx": current}
    trace: list[dict[str, Any]] = []
    sub_queries_used = 0
    total_steps = float(len(normalized["steps"]))

    async def _report(
        progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        if progress_callback is not None:
            try:
                result = progress_callback(progress, total, message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    for step_index, step in enumerate(normalized["steps"], 1):
        if step_index > max_steps:
            return False, {
                "error": f"Recipe exceeded budget.max_steps ({step_index} > {max_steps})",
                "failed_step": step_index,
                "trace": trace,
            }

        session.iterations += 1

        input_name = step.get("input")
        if input_name:
            if input_name not in variables:
                return False, {
                    "error": f"Step {step_index}: input variable '{input_name}' not found.",
                    "failed_step": step_index,
                    "trace": trace,
                }
            current = variables[input_name]

        op = step["op"]
        step_trace: dict[str, Any] = {
            "step": step_index,
            "op": op,
        }

        try:
            if op == "search":
                current = repl_helpers.search(
                    current,
                    step["pattern"],
                    context_lines=step.get("context_lines", 2),
                    max_results=step.get("max_results", 20),
                )
                step_trace["result_count"] = (
                    len(current) if isinstance(current, list) else 0
                )

            elif op == "peek":
                current = repl_helpers.peek(
                    current,
                    start=step.get("start", 0),
                    end=step.get("end"),
                )

            elif op == "lines":
                current = repl_helpers.lines(
                    current,
                    start=step.get("start", 0),
                    end=step.get("end"),
                )

            elif op == "take":
                count = int(step["count"])
                if isinstance(current, str):
                    current = current[:count]
                elif isinstance(current, (list, tuple)):
                    current = list(current)[:count]
                else:
                    raise ValueError("take requires a list/tuple/string value")

            elif op == "chunk":
                text = _coerce_context_to_text(current)
                chunk_size = int(step["chunk_size"])
                overlap = int(step.get("overlap", 0))
                current = repl_helpers.chunk(text, chunk_size, overlap)
                step_trace["result_count"] = len(current)

            elif op == "filter":
                if not isinstance(current, list):
                    raise ValueError("filter requires current value to be a list")
                field_name = step.get("field")
                pattern = step.get("pattern")
                contains = step.get("contains")
                rx = re.compile(pattern) if pattern else None
                out: list[Any] = []
                for item in current:
                    candidate: Any = item
                    if field_name:
                        if isinstance(item, dict):
                            candidate = item.get(field_name)
                        else:
                            candidate = None
                    candidate_text = _coerce_context_to_text(candidate)
                    matched = True
                    if rx is not None:
                        matched = bool(rx.search(candidate_text))
                    if contains is not None:
                        matched = matched and contains in candidate_text
                    if matched:
                        out.append(item)
                current = out
                step_trace["result_count"] = len(current)

            elif op == "assign":
                variables[step["name"]] = current

            elif op == "load":
                name = step["name"]
                if name not in variables:
                    raise ValueError(f"variable '{name}' not found")
                current = variables[name]

            elif op == "map_sub_query":
                if not isinstance(current, list):
                    raise ValueError(
                        "map_sub_query requires current value to be a list"
                    )

                limit = step.get("limit")
                items = current[:limit] if isinstance(limit, int) else current
                parallel = step.get("parallel", True)
                continue_on_error = step.get("continue_on_error", False)

                remaining_budget = max_sub_queries - sub_queries_used
                if len(items) > remaining_budget:
                    raise RuntimeError(
                        f"Recipe sub-query budget would be exceeded "
                        f"({sub_queries_used} + {len(items)} > {max_sub_queries})"
                    )

                if parallel and len(items) > 1:
                    parallel_limit = max(
                        1, min(owner.max_recipe_concurrency, len(items))
                    )
                    sem = asyncio.Semaphore(parallel_limit)

                    async def _run_item(
                        idx: int, item: object
                    ) -> tuple[int, bool, str]:
                        async with sem:
                            ctx_slice = recipe_context_slice(
                                item, step.get("context_field")
                            )
                            ok, out, _trunc, _bk = await owner._run_sub_query(
                                prompt=step["prompt"],
                                context_slice=ctx_slice,
                                context_id=resolved_context_id,
                                backend=step.get("backend", "auto"),
                            )
                            return idx, ok, out

                    tasks = [_run_item(i, it) for i, it in enumerate(items)]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    outputs: list[str] = [""] * len(items)
                    for task_idx, r in enumerate(results):
                        if isinstance(r, BaseException):
                            if not continue_on_error:
                                raise RuntimeError(f"sub_query failed: {r}")
                            outputs[task_idx] = f"[ERROR] {r}"
                        else:
                            idx, ok, item_output = r
                            if not ok and not continue_on_error:
                                raise RuntimeError(f"sub_query failed: {item_output}")
                            outputs[idx] = (
                                item_output if ok else f"[ERROR] {item_output}"
                            )
                    sub_queries_used += len(items)
                else:
                    outputs = []
                    for item in items:
                        context_slice = recipe_context_slice(
                            item, step.get("context_field")
                        )
                        (
                            success,
                            output,
                            _truncated,
                            _backend,
                        ) = await owner._run_sub_query(
                            prompt=step["prompt"],
                            context_slice=context_slice,
                            context_id=resolved_context_id,
                            backend=step.get("backend", "auto"),
                        )
                        sub_queries_used += 1
                        if not success and not continue_on_error:
                            raise RuntimeError(f"sub_query failed: {output}")
                        outputs.append(output if success else f"[ERROR] {output}")
                await _report(
                    float(step_index),
                    total_steps,
                    f"map_sub_query: {len(items)} items processed",
                )

                current = outputs
                step_trace["sub_queries"] = len(outputs)
                step_trace["parallel"] = parallel and len(items) > 1

            elif op in {"sub_query", "aggregate"}:
                if sub_queries_used >= max_sub_queries:
                    raise RuntimeError(
                        "Recipe sub-query budget exceeded "
                        f"({sub_queries_used} >= {max_sub_queries})"
                    )

                if op == "aggregate" and isinstance(current, list):
                    context_slice = "\n\n".join(
                        _coerce_context_to_text(item) for item in current
                    )
                else:
                    context_slice = recipe_context_slice(
                        current, step.get("context_field")
                    )

                success, output, _truncated, _backend = await owner._run_sub_query(
                    prompt=step["prompt"],
                    context_slice=context_slice,
                    context_id=resolved_context_id,
                    backend=step.get("backend", "auto"),
                )
                sub_queries_used += 1
                if not success:
                    raise RuntimeError(f"sub_query failed: {output}")
                current = output
                step_trace["sub_queries"] = 1

            elif op == "finalize":
                step_trace["status"] = "finalized"
                trace.append(step_trace)
                break

            else:
                raise ValueError(f"unsupported op: {op}")
        except Exception as e:
            step_trace["status"] = "error"
            step_trace["error"] = str(e)
            trace.append(step_trace)
            session.evidence.append(
                _Evidence(
                    source="exec",
                    line_range=None,
                    pattern=None,
                    note=f"run_recipe failed at step {step_index}",
                    snippet=f"{op}: {str(e)[:180]}",
                )
            )
            return False, {
                "error": f"Step {step_index} ({op}) failed: {e}",
                "failed_step": step_index,
                "trace": trace,
                "sub_queries_used": sub_queries_used,
                "budget": budget,
                "estimate": estimate,
            }

        store_name = step.get("store")
        if store_name:
            variables[store_name] = current

        step_trace["status"] = "ok"
        step_trace["preview"] = recipe_preview(current)
        trace.append(step_trace)
        await _report(
            float(step_index),
            total_steps,
            f"Step {step_index}/{int(total_steps)} ({op}) done",
        )

    session.evidence.append(
        _Evidence(
            source="exec",
            line_range=None,
            pattern=None,
            note=f"run_recipe completed ({len(trace)} steps)",
            snippet=recipe_preview(current),
        )
    )

    payload = {
        "context_id": resolved_context_id,
        "recipe_version": normalized["version"],
        "step_count": len(normalized["steps"]),
        "sub_queries_used": sub_queries_used,
        "budget": budget,
        "estimate": estimate,
        "trace": trace,
        "value": _to_jsonable(current),
        "variables": sorted(variables.keys()),
    }
    return True, payload


async def compile_recipe_code(
    owner: "AlephMCPServerLocal",
    *,
    code: str,
    context_id: str = "default",
    language: str = "python",
) -> tuple[bool, dict[str, Any]]:
    if context_id not in owner._sessions:
        return False, {"error": f"No context loaded with ID '{context_id}'."}

    session = owner._sessions[context_id]
    session.iterations += 1

    if language in ("javascript", "typescript"):
        node_repl = owner._get_or_create_node_repl(context_id)
        result = await node_repl.execute_async(
            code,
            language=language,  # type: ignore[arg-type]
        )
    else:
        result = await session.repl.execute_async(code)

    if result.error:
        return False, {
            "error": f"Recipe code execution failed: {result.error}",
            "execution": {
                "stderr": result.stderr,
                "stdout": result.stdout,
            },
        }

    if language in ("javascript", "typescript"):
        candidate = result.return_value
        if candidate is None:
            maybe_node_repl = owner._node_repls.get(context_id)
            if maybe_node_repl is not None:
                candidate = maybe_node_repl.get_variable("recipe")
        owner._sync_session_from_node_repl(context_id)
    else:
        candidate = result.return_value
        if candidate is None:
            candidate = session.repl.get_variable("recipe")

    if candidate is None:
        return False, {
            "error": (
                "Recipe code did not return a recipe value. "
                "Return a RecipeBuilder/dict or assign to variable `recipe`."
            ),
        }

    compiled: Any = candidate
    if isinstance(candidate, dict):
        compiled = dict(candidate)
    elif hasattr(candidate, "compile") and callable(getattr(candidate, "compile")):
        compiled = candidate.compile()
    elif hasattr(candidate, "to_dict") and callable(getattr(candidate, "to_dict")):
        compiled = candidate.to_dict()
    else:
        return False, {
            "error": (
                "Recipe code returned unsupported type. "
                "Expected dict or object with compile()/to_dict()."
            ),
            "type": str(type(candidate)),
        }

    normalized, errors = _validate_recipe(compiled)
    if errors or normalized is None:
        return False, {
            "error": "Compiled recipe is invalid.",
            "errors": errors,
            "compiled": _to_jsonable(compiled),
        }

    return True, {
        "context_id": context_id,
        "recipe": normalized,
        "estimate": _estimate_recipe(normalized),
        "execution": {
            "variables_updated": result.variables_updated,
            "execution_time_ms": result.execution_time_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    }

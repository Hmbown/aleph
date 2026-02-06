"""Recipe schema validation and estimation helpers for Aleph MCP pipelines."""

from __future__ import annotations

from collections import Counter
from typing import Any

RECIPE_SCHEMA_VERSION = "aleph.recipe.v1"

_ALLOWED_OPS: set[str] = {
    "search",
    "peek",
    "lines",
    "take",
    "chunk",
    "filter",
    "map_sub_query",
    "sub_query",
    "aggregate",
    "assign",
    "load",
    "finalize",
}

_ALLOWED_BACKENDS: set[str] = {"auto", "api", "claude", "codex", "gemini", "kimi"}


def _require_positive_int(
    value: Any,
    *,
    field: str,
    errors: list[str],
    min_value: int = 1,
) -> int | None:
    if not isinstance(value, int):
        errors.append(f"{field} must be an integer")
        return None
    if value < min_value:
        errors.append(f"{field} must be >= {min_value}")
        return None
    return value


def _normalize_budget(raw: Any, step_count: int, errors: list[str]) -> dict[str, int]:
    if raw is None:
        return {
            "max_steps": max(step_count, 1),
            "max_sub_queries": 20,
        }

    if not isinstance(raw, dict):
        errors.append("budget must be an object")
        return {
            "max_steps": max(step_count, 1),
            "max_sub_queries": 20,
        }

    max_steps = raw.get("max_steps", step_count)
    max_sub_queries = raw.get("max_sub_queries", 20)

    resolved_steps = _require_positive_int(max_steps, field="budget.max_steps", errors=errors)
    resolved_sub = _require_positive_int(max_sub_queries, field="budget.max_sub_queries", errors=errors, min_value=0)

    return {
        "max_steps": resolved_steps if resolved_steps is not None else max(step_count, 1),
        "max_sub_queries": resolved_sub if resolved_sub is not None else 20,
    }


def validate_recipe(recipe: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a recipe payload and return (normalized_recipe, errors)."""
    errors: list[str] = []

    if not isinstance(recipe, dict):
        return None, ["recipe must be an object"]

    version = recipe.get("version", RECIPE_SCHEMA_VERSION)
    if not isinstance(version, str):
        errors.append("version must be a string")
    elif version != RECIPE_SCHEMA_VERSION:
        errors.append(
            f"unsupported recipe version {version!r}; expected {RECIPE_SCHEMA_VERSION!r}"
        )

    context_id = recipe.get("context_id", "default")
    if not isinstance(context_id, str) or not context_id.strip():
        errors.append("context_id must be a non-empty string")
        context_id = "default"

    raw_steps = recipe.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        errors.append("steps must be a non-empty list")
        return None, errors

    normalized_steps: list[dict[str, Any]] = []

    for idx, raw_step in enumerate(raw_steps):
        path = f"steps[{idx}]"
        if not isinstance(raw_step, dict):
            errors.append(f"{path} must be an object")
            continue

        op = raw_step.get("op")
        if not isinstance(op, str) or not op.strip():
            errors.append(f"{path}.op must be a non-empty string")
            continue
        if op not in _ALLOWED_OPS:
            errors.append(f"{path}.op {op!r} is not supported")
            continue

        step: dict[str, Any] = {"op": op}

        input_name = raw_step.get("input")
        if input_name is not None:
            if not isinstance(input_name, str) or not input_name.strip():
                errors.append(f"{path}.input must be a non-empty string when provided")
            else:
                step["input"] = input_name

        store_name = raw_step.get("store")
        if store_name is not None:
            if not isinstance(store_name, str) or not store_name.strip():
                errors.append(f"{path}.store must be a non-empty string when provided")
            else:
                step["store"] = store_name

        if op == "search":
            pattern = raw_step.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                errors.append(f"{path}.pattern must be a non-empty string")
            else:
                step["pattern"] = pattern
            context_lines = raw_step.get("context_lines", 2)
            max_results = raw_step.get("max_results", 20)
            resolved_context_lines = _require_positive_int(
                context_lines,
                field=f"{path}.context_lines",
                errors=errors,
                min_value=0,
            )
            resolved_max_results = _require_positive_int(
                max_results,
                field=f"{path}.max_results",
                errors=errors,
            )
            if resolved_context_lines is not None:
                step["context_lines"] = resolved_context_lines
            if resolved_max_results is not None:
                step["max_results"] = resolved_max_results

        elif op in {"peek", "lines"}:
            start = raw_step.get("start", 0)
            end = raw_step.get("end")
            resolved_start = _require_positive_int(
                start,
                field=f"{path}.start",
                errors=errors,
                min_value=0,
            )
            if resolved_start is not None:
                step["start"] = resolved_start
            if end is not None:
                resolved_end = _require_positive_int(
                    end,
                    field=f"{path}.end",
                    errors=errors,
                    min_value=0,
                )
                if resolved_end is not None:
                    step["end"] = resolved_end
                    if resolved_start is not None and resolved_end < resolved_start:
                        errors.append(f"{path}.end must be >= {path}.start")

        elif op == "take":
            count = raw_step.get("count")
            resolved_count = _require_positive_int(count, field=f"{path}.count", errors=errors)
            if resolved_count is not None:
                step["count"] = resolved_count

        elif op == "chunk":
            chunk_size = raw_step.get("chunk_size")
            resolved_chunk = _require_positive_int(chunk_size, field=f"{path}.chunk_size", errors=errors)
            if resolved_chunk is not None:
                step["chunk_size"] = resolved_chunk
            overlap = raw_step.get("overlap", 0)
            resolved_overlap = _require_positive_int(
                overlap, field=f"{path}.overlap", errors=errors, min_value=0,
            )
            if resolved_overlap is not None:
                step["overlap"] = resolved_overlap
                if resolved_chunk is not None and resolved_overlap >= resolved_chunk:
                    errors.append(f"{path}.overlap must be < {path}.chunk_size")

        elif op == "filter":
            pattern = raw_step.get("pattern")
            contains = raw_step.get("contains")
            field_name = raw_step.get("field")

            if pattern is None and contains is None:
                errors.append(f"{path} requires either pattern or contains")
            if pattern is not None:
                if not isinstance(pattern, str) or not pattern:
                    errors.append(f"{path}.pattern must be a non-empty string")
                else:
                    step["pattern"] = pattern
            if contains is not None:
                if not isinstance(contains, str) or not contains:
                    errors.append(f"{path}.contains must be a non-empty string")
                else:
                    step["contains"] = contains
            if field_name is not None:
                if not isinstance(field_name, str) or not field_name.strip():
                    errors.append(f"{path}.field must be a non-empty string")
                else:
                    step["field"] = field_name

        elif op in {"map_sub_query", "sub_query", "aggregate"}:
            prompt = raw_step.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                errors.append(f"{path}.prompt must be a non-empty string")
            else:
                step["prompt"] = prompt

            backend = raw_step.get("backend", "auto")
            if not isinstance(backend, str) or backend not in _ALLOWED_BACKENDS:
                errors.append(
                    f"{path}.backend must be one of {sorted(_ALLOWED_BACKENDS)}"
                )
            else:
                step["backend"] = backend

            context_field = raw_step.get("context_field")
            if context_field is not None:
                if not isinstance(context_field, str) or not context_field.strip():
                    errors.append(f"{path}.context_field must be a non-empty string")
                else:
                    step["context_field"] = context_field

            limit = raw_step.get("limit")
            if limit is not None:
                resolved_limit = _require_positive_int(
                    limit,
                    field=f"{path}.limit",
                    errors=errors,
                )
                if resolved_limit is not None:
                    step["limit"] = resolved_limit

            continue_on_error = raw_step.get("continue_on_error", False)
            if not isinstance(continue_on_error, bool):
                errors.append(f"{path}.continue_on_error must be boolean")
            else:
                step["continue_on_error"] = continue_on_error

        elif op in {"assign", "load"}:
            name = raw_step.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{path}.name must be a non-empty string")
            else:
                step["name"] = name

        elif op == "finalize":
            pass

        normalized_steps.append(step)

    budget = _normalize_budget(recipe.get("budget"), len(normalized_steps), errors)

    if errors:
        return None, errors

    normalized = {
        "version": RECIPE_SCHEMA_VERSION,
        "context_id": context_id,
        "budget": budget,
        "steps": normalized_steps,
    }
    return normalized, []


def estimate_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    """Return a static estimate for recipe cost/shape."""
    steps = recipe.get("steps", [])
    budget = recipe.get("budget", {})

    op_counts = Counter()
    projected_sub_queries = 0
    projected_max_search_hits = 0
    warnings: list[str] = []

    previous_list_estimate = 10

    for step in steps:
        if not isinstance(step, dict):
            continue
        op = step.get("op")
        if not isinstance(op, str):
            continue
        op_counts[op] += 1

        if op == "search":
            max_results = int(step.get("max_results", 20))
            projected_max_search_hits += max_results
            previous_list_estimate = max_results

        elif op == "chunk":
            # Rough heuristic: assume context ~100K chars unless we know better
            chunk_size = int(step.get("chunk_size", 10000))
            estimated_chunks = max(1, 100_000 // chunk_size)
            previous_list_estimate = estimated_chunks

        elif op == "take":
            count = int(step.get("count", previous_list_estimate))
            previous_list_estimate = min(count, previous_list_estimate)

        elif op in {"sub_query", "aggregate"}:
            projected_sub_queries += 1

        elif op == "map_sub_query":
            if isinstance(step.get("limit"), int):
                projected_sub_queries += int(step["limit"])
            else:
                projected_sub_queries += previous_list_estimate

    max_sub_queries = budget.get("max_sub_queries")
    if isinstance(max_sub_queries, int) and projected_sub_queries > max_sub_queries:
        warnings.append(
            "Projected sub-query count exceeds budget.max_sub_queries "
            f"({projected_sub_queries} > {max_sub_queries})"
        )

    max_steps = budget.get("max_steps")
    if isinstance(max_steps, int) and len(steps) > max_steps:
        warnings.append(
            f"Recipe has more steps than budget.max_steps ({len(steps)} > {max_steps})"
        )

    return {
        "step_count": len(steps),
        "operation_counts": dict(op_counts),
        "projected_sub_queries": projected_sub_queries,
        "projected_max_search_hits": projected_max_search_hits,
        "warnings": warnings,
    }

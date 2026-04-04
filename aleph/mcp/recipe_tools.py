"""Recipe MCP tool registrations for the local server."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

from .recipes import estimate_recipe as _estimate_recipe
from .recipes import validate_recipe as _validate_recipe

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from .local_server import AlephMCPServerLocal
else:
    Context = Any


def register_recipe_tools(
    owner: "AlephMCPServerLocal",
    *,
    format_error: Callable[[str, Literal["json", "markdown", "object"]], str | dict[str, Any]],
) -> None:
    _tool = owner._tool_decorator

    @_tool()
    async def validate_recipe(
        recipe: dict[str, Any],
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Validate recipe structure and return normalized payload/errors."""
        normalized, errors = _validate_recipe(recipe)
        payload: dict[str, Any] = {
            "valid": not errors,
            "errors": errors,
        }
        if normalized is not None:
            payload["recipe"] = normalized
            payload["estimate"] = _estimate_recipe(normalized)

        if output == "object":
            return payload
        if output == "json":
            return json.dumps(payload, indent=2)
        if errors:
            lines = ["## Recipe Validation", "", "**Status:** invalid", "", "**Errors:**"]
            lines.extend(f"- {err}" for err in errors)
            return "\n".join(lines)
        return "## Recipe Validation\n\n**Status:** valid"

    @_tool()
    async def estimate_recipe(
        recipe: dict[str, Any],
        context_id: str | None = None,
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Static estimate for recipe execution cost and shape."""
        normalized, errors = _validate_recipe(recipe)
        if errors or normalized is None:
            return format_error(
                "Invalid recipe: " + "; ".join(errors),
                output=output,
            )

        estimate = _estimate_recipe(normalized)
        resolved_context_id = context_id or normalized["context_id"]
        payload: dict[str, Any] = {
            "context_id": resolved_context_id,
            "estimate": estimate,
            "budget": normalized["budget"],
        }
        if resolved_context_id in owner._sessions:
            session = owner._sessions[resolved_context_id]
            payload["context_size"] = {
                "chars": session.meta.size_chars,
                "lines": session.meta.size_lines,
            }

        if output == "object":
            return payload
        if output == "json":
            return json.dumps(payload, indent=2)
        lines = [
            "## Recipe Estimate",
            "",
            f"- Context: `{resolved_context_id}`",
            f"- Steps: {estimate['step_count']}",
            f"- Projected sub-queries: {estimate['projected_sub_queries']}",
            f"- Projected max search hits: {estimate['projected_max_search_hits']}",
        ]
        warnings = estimate.get("warnings", [])
        if warnings:
            lines.append("")
            lines.append("**Warnings:**")
            lines.extend(f"- {warning}" for warning in warnings)
        return "\n".join(lines)

    @_tool()
    async def run_recipe(
        recipe: dict[str, Any],
        context_id: str | None = None,
        dry_run: bool = False,
        output: Literal["markdown", "json", "object"] = "markdown",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Execute a declarative recipe pipeline."""
        progress_cb = ctx.report_progress if ctx is not None else None
        ok, payload = await owner._execute_recipe(
            recipe=recipe,
            context_id_override=context_id,
            dry_run=dry_run,
            progress_callback=progress_cb,
        )
        full_payload: dict[str, Any] = {"success": ok, **payload}
        if output == "object":
            return full_payload
        if output == "json":
            return json.dumps(full_payload, indent=2)
        if not ok:
            message = payload.get("error")
            if not message and "errors" in payload:
                message = "; ".join(str(err) for err in payload.get("errors", []))
            return format_error(str(message or "Recipe execution failed"), output=output)

        trace = payload.get("trace", [])
        lines = [
            "## Recipe Run",
            "",
            f"- Context: `{payload.get('context_id')}`",
            f"- Steps executed: {len(trace)}",
            f"- Sub-queries used: {payload.get('sub_queries_used', 0)}",
            "",
            "**Final Value Preview:**",
            "```",
            owner._recipe_preview(payload.get("value")),
            "```",
        ]
        return "\n".join(lines)

    @_tool()
    async def compile_recipe(
        code: str,
        context_id: str = "default",
        language: Literal["python", "javascript", "typescript"] = "python",
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Compile Recipe DSL code into a validated recipe payload.

        Use ``language="javascript"`` or ``language="typescript"`` to write
        recipe DSL code in JS/TS instead of the default Python.
        """
        ok, payload = await owner._compile_recipe_code(
            code=code, context_id=context_id, language=language,
        )
        full_payload: dict[str, Any] = {"success": ok, **payload}
        if output == "object":
            return full_payload
        if output == "json":
            return json.dumps(full_payload, indent=2)
        if not ok:
            return format_error(str(payload.get("error", "Recipe compile failed")), output=output)

        recipe_payload = payload.get("recipe", {})
        estimate = payload.get("estimate", {})
        lines = [
            "## Recipe Compile",
            "",
            f"- Context: `{context_id}`",
            f"- Steps: {estimate.get('step_count', 0)}",
            f"- Projected sub-queries: {estimate.get('projected_sub_queries', 0)}",
            "",
            "**Recipe JSON:**",
            "```json",
            json.dumps(recipe_payload, indent=2),
            "```",
        ]
        return "\n".join(lines)

    @_tool()
    async def run_recipe_code(
        code: str,
        context_id: str = "default",
        language: Literal["python", "javascript", "typescript"] = "python",
        dry_run: bool = False,
        output: Literal["markdown", "json", "object"] = "markdown",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str | dict[str, Any]:
        """Compile Recipe DSL code and execute it.

        Use ``language="javascript"`` or ``language="typescript"`` to write
        recipe DSL code in JS/TS instead of the default Python.
        """
        ok_compile, compile_payload = await owner._compile_recipe_code(
            code=code,
            context_id=context_id,
            language=language,
        )
        if not ok_compile:
            if output == "object":
                return {"success": False, **compile_payload}
            if output == "json":
                return json.dumps({"success": False, **compile_payload}, indent=2)
            return format_error(
                str(compile_payload.get("error", "Recipe compile failed")),
                output=output,
            )

        progress_cb = ctx.report_progress if ctx is not None else None
        compiled_recipe = cast(dict[str, Any], compile_payload["recipe"])
        ok_run, run_payload = await owner._execute_recipe(
            recipe=compiled_recipe,
            context_id_override=context_id,
            dry_run=dry_run,
            progress_callback=progress_cb,
        )
        full_payload = {
            "success": ok_run,
            "compiled": compile_payload,
            "run": run_payload,
        }
        if output == "object":
            return full_payload
        if output == "json":
            return json.dumps(full_payload, indent=2)
        if not ok_run:
            return format_error(
                str(run_payload.get("error", "Recipe execution failed")),
                output=output,
            )

        trace = run_payload.get("trace", [])
        lines = [
            "## Recipe Code Run",
            "",
            f"- Context: `{run_payload.get('context_id')}`",
            f"- Steps executed: {len(trace)}",
            f"- Sub-queries used: {run_payload.get('sub_queries_used', 0)}",
            "",
            "**Final Value Preview:**",
            "```",
            owner._recipe_preview(run_payload.get("value")),
            "```",
        ]
        return "\n".join(lines)

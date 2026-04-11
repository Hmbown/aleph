"""Reasoning MCP tool registrations for the local server."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Literal

from .recipe_tools import register_recipe_tools
from .workspace_contexts import binding_status, binding_summary

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from .local_server import AlephMCPServerLocal
else:
    Context = Any


def register_reasoning_tools(
    owner: "AlephMCPServerLocal",
    *,
    format_error: Callable[[str, Literal["json", "markdown", "object"]], str | dict[str, Any]],
) -> None:
    _tool = owner._tool_decorator

    def _task_store(session: Any) -> list[dict[str, Any]]:
        namespace_tasks = session.repl._namespace.get("_tasks")  # type: ignore[attr-defined]
        if isinstance(namespace_tasks, list):
            if session.tasks is not namespace_tasks:
                session.tasks = namespace_tasks
            return namespace_tasks
        session.repl._namespace["_tasks"] = session.tasks  # type: ignore[attr-defined]
        return session.tasks

    @_tool()
    async def think(
        question: str,
        context_slice: str | None = None,
        context_id: str = "default",
    ) -> str:
        """Structure a reasoning sub-step."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        session.iterations += 1
        session.think_history.append(question)

        log_entry = {
            "iteration": session.iterations,
            "question": question,
            "context_slice": context_slice[:200] if context_slice else None,
            "timestamp": datetime.now().isoformat(),
        }
        session.repl._namespace.setdefault("_reasoning_trace", []).append(log_entry)  # type: ignore

        res = [
            "## Reasoning Step",
            "",
            f"**Question:** {question}",
        ]
        if context_slice:
            res.append(
                "\n---\n\n*Context snippet captured in internal trace (not echoed to avoid context bloat).*"
            )

        res.append("\n---\n\n**Your task:** Reason through this step-by-step. Consider:")
        res.append("1. What information do you have?")
        res.append("2. What can you infer?")
        res.append("3. What's the answer to this sub-question?")
        res.append("\n*After reasoning, use `exec_python` to verify or `finalize` if done.*")

        return "\n".join(res)

    @_tool()
    async def tasks(
        action: Literal["list", "add", "update", "clear"] = "list",
        task_id: str | None = None,
        description: str | None = None,
        status: Literal["todo", "done", "blocked"] = "todo",
        context_id: str = "default",
    ) -> str | dict[str, Any]:
        """Track tasks attached to a context."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        session.iterations += 1
        tasks_list = _task_store(session)

        if action == "add" and description:
            new_id = task_id or f"T{len(tasks_list) + 1}"
            tasks_list.append({"id": new_id, "description": description, "status": status})
            return f"Task {new_id} added."

        if action == "update" and task_id:
            for task in tasks_list:
                if task["id"] == task_id:
                    if description:
                        task["description"] = description
                    task["status"] = status
                    return f"Task {task_id} updated."
            return f"Error: Task {task_id} not found."

        if action == "clear":
            tasks_list.clear()
            return "All tasks cleared."

        if not tasks_list:
            return "No tasks tracked for this context."

        res = ["## Task List\n"]
        for task in tasks_list:
            icon = "✅" if task["status"] == "done" else "⏳" if task["status"] == "todo" else "🚫"
            res.append(f"- {icon} **{task['id']}**: {task['description']}")
        return "\n".join(res)

    @_tool()
    async def get_status(
        context_id: str = "default",
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Session state."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        tasks_list = _task_store(session)
        status = {
            "context_id": context_id,
            "iterations": session.iterations,
            "evidence_count": len(session.evidence),
            "tasks_count": len(tasks_list),
            "variables": [k for k in session.repl._namespace.keys() if not k.startswith("_")],
            "size_chars": session.meta.size_chars,
            "size_lines": session.meta.size_lines,
            "workspace_root": str(owner.action_config.workspace_root),
            "workspace_root_source": owner._workspace_root_source,
            "context_policy": owner.context_policy,
            "action_policy": owner.action_config.action_policy,
            "auto_memory_pack": owner.context_policy != "isolated",
            "workspace_binding": session.workspace_binding,
            "workspace_binding_summary": binding_summary(session.workspace_binding),
            "workspace_binding_status": binding_status(session.workspace_binding),
        }

        if output == "object":
            return status
        if output == "json":
            return json.dumps(status, indent=2)

        res = [f"## Session Status: {context_id}\n"]
        res.append(f"- **Iterations**: {session.iterations}")
        res.append(f"- **Evidence Items**: {len(session.evidence)}")
        res.append(f"- **Tracked Tasks**: {len(tasks_list)}")
        res.append(f"- **User Variables**: {', '.join(status['variables']) or 'None'}")  # type: ignore
        res.append(f"- **Context Size**: {session.meta.size_chars:,} chars ({session.meta.size_lines:,} lines)")
        res.append(f"- **Workspace Root**: {owner.action_config.workspace_root} ({owner._workspace_root_source})")
        res.append(f"- **Context Policy**: {owner.context_policy}")
        res.append(f"- **Action Policy**: {owner.action_config.action_policy}")
        if status["workspace_binding_summary"]:
            res.append(f"- **Workspace Binding**: {status['workspace_binding_summary']}")
        return "\n".join(res)

    @_tool()
    async def get_evidence(
        limit: int = 20,
        offset: int = 0,
        source: Literal["any", "search", "peek", "exec", "manual", "action"] = "any",
        context_id: str = "default",
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Retrieve collected evidence/citations for a session."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        filtered = session.evidence
        if source != "any":
            filtered = [e for e in filtered if e.source == source]

        count = len(filtered)
        window = filtered[offset : offset + limit]

        items = []
        for ev in window:
            items.append(
                {
                    "source": ev.source,
                    "line_range": ev.line_range,
                    "pattern": ev.pattern,
                    "note": ev.note,
                    "snippet": ev.snippet,
                }
            )

        if output == "object":
            return {"total": count, "items": items}
        if output == "json":
            return json.dumps({"total": count, "items": items}, indent=2)

        if not items:
            return "No evidence found matching criteria."

        res = [f"## Evidence Log (Total: {count})\n"]
        for i, item in enumerate(items, offset + 1):
            source_info = f"[{item['source']}]"
            lr = item["line_range"]
            if isinstance(lr, (list, tuple)) and len(lr) >= 2:
                source_info += f" lines {lr[0]}-{lr[1]}"
            if item["pattern"]:
                source_info += f" pattern: `{item['pattern']}`"
            if item["note"]:
                source_info += f" note: {item['note']}"
            res.append(f"{i}. {source_info}: \"{item['snippet'][:100]}...\"")
        return "\n".join(res)

    @_tool()
    async def finalize(
        answer: str,
        confidence: Literal["high", "medium", "low"] = "medium",
        reasoning_summary: str | None = None,
        context_id: str = "default",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str:
        """Mark the task complete with your final answer."""
        if ctx is not None:
            await owner._maybe_resolve_workspace_from_roots(ctx)

        parts = ["## Final Answer", "", answer]
        if reasoning_summary:
            parts.extend(["", "---", "", f"**Reasoning:** {reasoning_summary}"])

        if context_id in owner._sessions:
            session = owner._sessions[context_id]
            parts.extend(["", f"*Completed after {session.iterations} iterations.*"])

        parts.append(f"\n**Confidence:** {confidence}")

        if context_id in owner._sessions:
            session = owner._sessions[context_id]
            if session.evidence:
                parts.extend(["", "---", "", "### Evidence Citations"])
                parts.append(
                    f"*Line numbers are {'1-based' if session.line_number_base == 1 else '0-based'}.*"
                )
                for i, ev in enumerate(session.evidence[-10:], 1):
                    source_info = f"[{ev.source}]"
                    if ev.line_range:
                        source_info += f" lines {ev.line_range[0]}-{ev.line_range[1]}"
                    if ev.pattern:
                        source_info += f" pattern: `{ev.pattern}`"
                    if ev.note:
                        source_info += f" note: {ev.note}"
                    parts.append(
                        f"{i}. {source_info}: \"{ev.snippet[:80]}...\""
                        if len(ev.snippet) > 80
                        else f"{i}. {source_info}: \"{ev.snippet}\""
                    )

        owner._auto_save_memory_pack()
        return "\n".join(parts)

    @_tool()
    async def evaluate_progress(
        current_understanding: str,
        remaining_questions: list[str] | str | None = None,
        confidence_score: float = 0.5,
        context_id: str = "default",
    ) -> str:
        """Self-evaluate your progress."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        session.iterations += 1

        res = [
            "## Progress Evaluation",
            "",
            f"**Current Understanding:** {current_understanding}",
            f"\n**Confidence Score:** {confidence_score:.2f}",
        ]
        if remaining_questions:
            res.append("\n**Remaining Questions:**")
            if isinstance(remaining_questions, str):
                res.append(f"- {remaining_questions}")
            else:
                for question in remaining_questions:
                    res.append(f"- {question}")

        if confidence_score > 0.9:
            res.append("\n*Confidence is high. Consider finalizing if the goal is met.*")
        elif confidence_score < 0.3:
            res.append("\n*Confidence is low. Try a different search pattern or tool.*")

        return "\n".join(res)

    @_tool()
    async def summarize_so_far(
        context_id: str = "default",
        include_evidence: bool = True,
        include_variables: bool = True,
        clear_history: bool = False,
    ) -> str:
        """Compress reasoning history to manage context window."""
        if context_id not in owner._sessions:
            return f"Error: No context loaded with ID '{context_id}'."

        session = owner._sessions[context_id]
        session.iterations += 1

        summary = f"Session '{context_id}' has run for {session.iterations} iterations."
        summary += f" Context size: {session.meta.size_chars:,} chars."

        if include_evidence and session.evidence:
            summary += f"\nEvidence collected: {len(session.evidence)} items."
        if include_variables:
            variables = [k for k in session.repl._namespace.keys() if not k.startswith("_")]
            if variables:
                summary += f"\nVariables defined: {', '.join(variables)}."

        return f"## Summary So Far\n\n{summary}\n\n*Use this summary to keep your focus sharp.*"

    register_recipe_tools(owner, format_error=format_error)

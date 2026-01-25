"""Aleph core implementation.

Aleph is a production-oriented implementation of Recursive Language Models
(RLMs): instead of stuffing massive context into an LLM prompt, Aleph stores the
context as a variable in a sandboxed REPL and lets the model write code to
inspect, search, and chunk that context.

The root LLM runs a loop:
1. It produces either Python code (```python) or a final answer (FINAL(...)).
2. Aleph executes the code in the REPL, captures output, and feeds the output back.
3. The model iterates until it emits FINAL(answer) or FINAL_VAR(name).

Sub-queries are supported via `sub_query(...)` and deeper recursion via
`sub_aleph(...)` available inside the REPL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from typing import Awaitable, Callable, cast

from .types import (
    ActionType,
    AlephResponse,
    Budget,
    BudgetStatus,
    ContextCollection,
    ContextMetadata,
    ContextType,
    ContentFormat,
    ExecutionResult,
    ParsedAction,
    SubQueryResult,
    SubQueryFn,
    TrajectoryStep,
    Message,
    SubAlephFn,
)
from .providers.base import LLMProvider, ProviderError
from .providers.registry import get_provider
from .cache.memory import MemoryCache
from .repl.sandbox import REPLEnvironment, SandboxConfig
from .prompts.system import DEFAULT_SYSTEM_PROMPT


_FINAL_RE = re.compile(r"FINAL\((.*?)\)", re.DOTALL)
_FINAL_VAR_RE = re.compile(r"FINAL_VAR\((.*?)\)", re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


class Aleph:
    """Recursive Language Model runner."""

    def __init__(
        self,
        provider: LLMProvider | str = "anthropic",
        root_model: str = "claude-sonnet-4-20250514",
        sub_model: str | None = None,
        budget: Budget | None = None,
        sandbox_config: SandboxConfig | None = None,
        system_prompt: str | None = None,
        enable_caching: bool = True,
        log_trajectory: bool = True,
        context_var_name: str = "ctx",
    ) -> None:
        """Create an Aleph runner.

        Args:
            provider: LLM provider instance or provider name.
            root_model: Model used for the root loop.
            sub_model: Model used for sub-queries/sub-aleph (defaults to root_model).
            budget: Resource limits (tokens/iterations/depth/wall-time/sub-queries).
            sandbox_config: REPL sandbox limits and allowed imports.
            system_prompt: Custom system prompt template.
            enable_caching: Enable memoization for sub-queries.
            log_trajectory: Record a full trajectory in the response.
            context_var_name: Variable name used to expose context in the REPL.
        """
        if isinstance(provider, str):
            self.provider = get_provider(provider)
        else:
            self.provider = provider

        self.root_model = root_model
        self.sub_model = sub_model or root_model
        self.budget = budget or Budget()
        self.sandbox_config = sandbox_config or SandboxConfig()
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.enable_caching = enable_caching
        self.log_trajectory = log_trajectory
        self.context_var_name = context_var_name

        self._cache: MemoryCache[str] | None = MemoryCache() if enable_caching else None

    async def complete(self, query: str, context: ContextType, **kwargs: object) -> AlephResponse:
        """Answer `query` using `context` via the RLM loop."""

        # Allow per-call overrides
        root_model = cast(str, kwargs.get("root_model", self.root_model))
        sub_model = cast(str, kwargs.get("sub_model", self.sub_model))

        budget_obj = kwargs.get("budget", self.budget)
        budget = budget_obj if isinstance(budget_obj, Budget) else self.budget

        temperature_obj = kwargs.get("temperature", 0.0)
        if isinstance(temperature_obj, (int, float)):
            temperature = float(temperature_obj)
        elif isinstance(temperature_obj, str):
            try:
                temperature = float(temperature_obj)
            except ValueError:
                temperature = 0.0
        else:
            temperature = 0.0

        start_time = time.time()
        budget_status = BudgetStatus(depth_current=0)
        trajectory: list[TrajectoryStep] = []

        # Global step counter across root steps + subcalls
        step_counter = 0
        step_lock = asyncio.Lock()

        # A helper to allocate a new step number in a concurrency-safe way
        async def next_step_number() -> int:
            nonlocal step_counter
            async with step_lock:
                step_counter += 1
                return step_counter

        # Run the root call
        response = await self._run(
            query=query,
            context=context,
            depth=0,
            root_model=root_model,
            sub_model=sub_model,
            budget=budget,
            budget_status=budget_status,
            start_time=start_time,
            trajectory=trajectory,
            temperature=temperature,
            next_step_number=next_step_number,
        )

        # Fill in top-level stats
        return response

    def complete_sync(self, query: str, context: ContextType, **kwargs: object) -> AlephResponse:
        """Synchronous wrapper around :meth:`complete`.

        Note: cannot be called from within an existing asyncio event loop.
        """

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            raise RuntimeError("complete_sync() cannot be called from a running event loop. Use `await aleph.complete(...)`.")

        return asyncio.run(self.complete(query, context, **kwargs))

    # ---------------------------------------------------------------------
    # Internal execution
    # ---------------------------------------------------------------------

    async def _run(
        self,
        query: str,
        context: ContextType,
        depth: int,
        root_model: str,
        sub_model: str,
        budget: Budget,
        budget_status: BudgetStatus,
        start_time: float,
        trajectory: list[TrajectoryStep],
        temperature: float,
        next_step_number: Callable[[], Awaitable[int]],
    ) -> AlephResponse:
        """Internal runner used for recursion."""

        # Depth check early
        if budget.max_depth is not None and depth > budget.max_depth:
            return AlephResponse(
                answer="",
                success=False,
                total_iterations=0,
                max_depth_reached=depth,
                total_tokens=budget_status.tokens_used,
                total_cost_usd=budget_status.cost_used,
                wall_time_seconds=time.time() - start_time,
                trajectory=trajectory,
                error=f"Max depth exceeded: depth={depth} > max_depth={budget.max_depth}",
                error_type="budget_exceeded",
            )

        budget_status.depth_current = max(budget_status.depth_current, depth)

        # Analyze context and create REPL
        meta = self._analyze_context(context)
        loop = asyncio.get_running_loop()
        repl = REPLEnvironment(
            context=context,
            context_var_name=self.context_var_name,
            config=self.sandbox_config,
            loop=loop,
        )

        # Inject sub_query and sub_aleph
        repl.inject_sub_query(self._make_sub_query(
            depth=depth,
            sub_model=sub_model,
            budget=budget,
            budget_status=budget_status,
            start_time=start_time,
            trajectory=trajectory,
            next_step_number=next_step_number,
            temperature=temperature,
        ))
        repl.inject_sub_aleph(self._make_sub_aleph(
            depth=depth,
            root_model=root_model,
            sub_model=sub_model,
            budget=budget,
            budget_status=budget_status,
            start_time=start_time,
            trajectory=trajectory,
            temperature=temperature,
            next_step_number=next_step_number,
        ))

        messages = self._build_initial_messages(query, meta)

        max_iterations = budget.max_iterations or 100
        local_iterations = 0

        max_depth_reached = depth

        while local_iterations < max_iterations:
            local_iterations += 1
            # Global iteration counter (across recursion)
            budget_status.iterations_used += 1
            budget_status.wall_time_used = time.time() - start_time

            exceeded, reason = budget_status.exceeds(budget)
            if exceeded:
                return AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=budget_status.iterations_used,
                    max_depth_reached=max_depth_reached,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                    error=reason,
                    error_type="budget_exceeded",
                )

            # Keep messages within context window (best-effort)
            self._trim_messages(messages, model=root_model)

            # Calculate remaining wall-time for timeout enforcement
            remaining_time: float | None = None
            if budget.max_wall_time_seconds is not None:
                remaining_time = budget.max_wall_time_seconds - budget_status.wall_time_used
                if remaining_time <= 0:
                    return AlephResponse(
                        answer="",
                        success=False,
                        total_iterations=budget_status.iterations_used,
                        max_depth_reached=max_depth_reached,
                        total_tokens=budget_status.tokens_used,
                        total_cost_usd=budget_status.cost_used,
                        wall_time_seconds=time.time() - start_time,
                        trajectory=trajectory,
                        error="Wall-time budget exhausted before provider call",
                        error_type="budget_exceeded",
                    )

            # Call provider with wall-time enforcement
            try:
                out_limit = self.provider.get_output_limit(root_model)
                max_tokens = min(out_limit, 8192)
                provider_coro = self.provider.complete(
                    messages=messages,
                    model=root_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if remaining_time is not None:
                    llm_text, in_tok, out_tok, cost = await asyncio.wait_for(
                        provider_coro, timeout=remaining_time
                    )
                else:
                    llm_text, in_tok, out_tok, cost = await provider_coro
            except asyncio.TimeoutError:
                return AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=budget_status.iterations_used,
                    max_depth_reached=max_depth_reached,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                    error="Wall-time budget exceeded during provider call",
                    error_type="budget_exceeded",
                )
            except ProviderError as e:
                return AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=budget_status.iterations_used,
                    max_depth_reached=max_depth_reached,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                    error=str(e),
                    error_type="provider_error",
                )
            except Exception as e:
                return AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=budget_status.iterations_used,
                    max_depth_reached=max_depth_reached,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                    error=f"Unexpected provider error: {e}",
                    error_type="provider_error",
                )

            budget_status.tokens_used += int(in_tok + out_tok)
            budget_status.cost_used += float(cost)
            budget_status.wall_time_used = time.time() - start_time

            # Stop immediately if the call pushed us over budget.
            exceeded, reason = budget_status.exceeds(budget)
            if exceeded:
                if self.log_trajectory:
                    step_no = await next_step_number()
                    trajectory.append(
                        TrajectoryStep(
                            step_number=step_no,
                            depth=depth,
                            timestamp=datetime.now(),
                            prompt_tokens=int(in_tok),
                            prompt_summary=(messages[-1].get("content", "")[:500]),
                            action=self._parse_response(llm_text),
                            result="[BUDGET_EXCEEDED_AFTER_PROVIDER_CALL]",
                            result_tokens=int(out_tok),
                            cumulative_tokens=budget_status.tokens_used,
                            cumulative_cost=budget_status.cost_used,
                        )
                    )

                return AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=budget_status.iterations_used,
                    max_depth_reached=max_depth_reached,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                    error=reason,
                    error_type="budget_exceeded",
                )

            action = self._parse_response(llm_text)

            # FINAL(answer)
            if action.action_type == ActionType.FINAL_ANSWER:
                answer = self._extract_final(llm_text)
                if self.log_trajectory:
                    step_no = await next_step_number()
                    trajectory.append(
                        TrajectoryStep(
                            step_number=step_no,
                            depth=depth,
                            timestamp=datetime.now(),
                            prompt_tokens=int(in_tok),
                            prompt_summary=(messages[-1].get("content", "")[:500]),
                            action=action,
                            result=answer,
                            result_tokens=int(out_tok),
                            cumulative_tokens=budget_status.tokens_used,
                            cumulative_cost=budget_status.cost_used,
                        )
                    )
                return AlephResponse(
                    answer=answer,
                    success=True,
                    total_iterations=budget_status.iterations_used,
                    max_depth_reached=max_depth_reached,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                )

            # FINAL_VAR(name)
            if action.action_type == ActionType.FINAL_VAR:
                var_name = self._extract_final_var(llm_text)
                value = repl.get_variable(var_name)
                if value is None:
                    answer = f"[ERROR: Variable '{var_name}' not found]"
                else:
                    answer = str(value)
                if self.log_trajectory:
                    step_no = await next_step_number()
                    trajectory.append(
                        TrajectoryStep(
                            step_number=step_no,
                            depth=depth,
                            timestamp=datetime.now(),
                            prompt_tokens=int(in_tok),
                            prompt_summary=(messages[-1].get("content", "")[:500]),
                            action=action,
                            result=answer,
                            result_tokens=int(out_tok),
                            cumulative_tokens=budget_status.tokens_used,
                            cumulative_cost=budget_status.cost_used,
                        )
                    )
                return AlephResponse(
                    answer=answer,
                    success=True,
                    total_iterations=budget_status.iterations_used,
                    max_depth_reached=max_depth_reached,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                )

            # CODE
            if action.action_type == ActionType.CODE_BLOCK:
                exec_result = await repl.execute_async(action.content)

                # Log trajectory step
                if self.log_trajectory:
                    step_no = await next_step_number()
                    result_text_for_tokens = exec_result.stdout
                    if exec_result.stderr:
                        result_text_for_tokens += "\n" + exec_result.stderr

                    trajectory.append(
                        TrajectoryStep(
                            step_number=step_no,
                            depth=depth,
                            timestamp=datetime.now(),
                            prompt_tokens=int(in_tok),
                            prompt_summary=(messages[-1].get("content", "")[:500]),
                            action=action,
                            result=exec_result,
                            result_tokens=self.provider.count_tokens(result_text_for_tokens, root_model),
                            cumulative_tokens=budget_status.tokens_used,
                            cumulative_cost=budget_status.cost_used,
                        )
                    )

                # Add the assistant response and REPL output back into the conversation
                messages.append({"role": "assistant", "content": llm_text})
                messages.append(
                    {
                        "role": "user",
                        "content": self._format_repl_result(exec_result),
                    }
                )

                # Track depth reached if subcalls happened
                max_depth_reached = max(max_depth_reached, budget_status.depth_current)
                continue

            # CONTINUE / unknown
            if self.log_trajectory:
                step_no = await next_step_number()
                trajectory.append(
                    TrajectoryStep(
                        step_number=step_no,
                        depth=depth,
                        timestamp=datetime.now(),
                        prompt_tokens=int(in_tok),
                        prompt_summary=(messages[-1].get("content", "")[:500]),
                        action=action,
                        result="[CONTINUE]",
                        result_tokens=int(out_tok),
                        cumulative_tokens=budget_status.tokens_used,
                        cumulative_cost=budget_status.cost_used,
                    )
                )
            messages.append({"role": "assistant", "content": llm_text})
            messages.append(
                {
                    "role": "user",
                    "content": "Continue. When you have the answer, use FINAL(answer) or FINAL_VAR(variable_name).",
                }
            )

            max_depth_reached = max(max_depth_reached, budget_status.depth_current)

        return AlephResponse(
            answer="",
            success=False,
            total_iterations=budget_status.iterations_used,
            max_depth_reached=max_depth_reached,
            total_tokens=budget_status.tokens_used,
            total_cost_usd=budget_status.cost_used,
            wall_time_seconds=time.time() - start_time,
            trajectory=trajectory,
            error="Max iterations reached without a final answer",
            error_type="max_iterations",
        )

    # ---------------------------------------------------------------------
    # Sub-calls (sub_query, sub_aleph)
    # ---------------------------------------------------------------------

    def _make_sub_query(
        self,
        depth: int,
        sub_model: str,
        budget: Budget,
        budget_status: BudgetStatus,
        start_time: float,
        trajectory: list[TrajectoryStep],
        next_step_number: Callable[[], Awaitable[int]],
        temperature: float,
    ) -> SubQueryFn:
        """Create an async sub_query function for the REPL."""

        async def sub_query(prompt: str, context_slice: str | None = None) -> str:
            # Budget checks
            budget_status.wall_time_used = time.time() - start_time

            if budget.max_sub_queries is not None and budget_status.sub_queries_used >= budget.max_sub_queries:
                return "[ERROR: Sub-query budget exceeded]"
            if budget.max_depth is not None and (depth + 1) > budget.max_depth:
                return "[ERROR: Max recursion depth reached]"

            # Cache key
            cache_key = None
            if self._cache is not None:
                h = hashlib.sha256()
                h.update(sub_model.encode())
                h.update(b"\0")
                h.update(prompt.encode())
                h.update(b"\0")
                if context_slice:
                    h.update(context_slice.encode())
                cache_key = f"subq:{h.hexdigest()}"
                cached = self._cache.get(cache_key)
                if isinstance(cached, str):
                    return cached

            messages: list[Message] = [{"role": "user", "content": prompt}]
            if context_slice:
                messages[0]["content"] = f"{prompt}\n\nContext:\n{context_slice}"

            out_limit = self.provider.get_output_limit(sub_model)
            max_tokens = min(out_limit, 4096)

            try:
                text, in_tok, out_tok, cost = await self.provider.complete(
                    messages=messages,
                    model=sub_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as e:
                return f"[ERROR: sub_query failed: {e}]"

            budget_status.tokens_used += int(in_tok + out_tok)
            budget_status.cost_used += float(cost)
            budget_status.sub_queries_used += 1
            budget_status.depth_current = max(budget_status.depth_current, depth + 1)
            budget_status.wall_time_used = time.time() - start_time

            exceeded, reason = budget_status.exceeds(budget)
            if exceeded:
                return f"[ERROR: Budget exceeded after sub_query: {reason}]"

            # Log sub-query as trajectory step
            if self.log_trajectory:
                step_no = await next_step_number()
                trajectory.append(
                    TrajectoryStep(
                        step_number=step_no,
                        depth=depth + 1,
                        timestamp=datetime.now(),
                        prompt_tokens=int(in_tok),
                        prompt_summary=prompt[:500],
                        action=ParsedAction(
                            action_type=ActionType.TOOL_CALL,
                            content="sub_query",
                            raw_response="sub_query(...)",
                        ),
                        result=SubQueryResult(
                            answer=text,
                            tokens_input=int(in_tok),
                            tokens_output=int(out_tok),
                            cost_usd=float(cost),
                            model_used=sub_model,
                            depth=depth + 1,
                        ),
                        result_tokens=int(out_tok),
                        cumulative_tokens=budget_status.tokens_used,
                        cumulative_cost=budget_status.cost_used,
                    )
                )

            if cache_key and self._cache is not None:
                self._cache.set(cache_key, text)

            return text

        return sub_query

    def _make_sub_aleph(
        self,
        depth: int,
        root_model: str,
        sub_model: str,
        budget: Budget,
        budget_status: BudgetStatus,
        start_time: float,
        trajectory: list[TrajectoryStep],
        temperature: float,
        next_step_number: Callable[[], Awaitable[int]],
    ) -> SubAlephFn:
        """Create an async sub_aleph function for the REPL."""

        async def sub_aleph(query: str, context: ContextType | None = None) -> AlephResponse:
            budget_status.wall_time_used = time.time() - start_time

            if budget.max_depth is not None and (depth + 1) > budget.max_depth:
                return AlephResponse(
                    answer="",
                    success=False,
                    total_iterations=0,
                    max_depth_reached=depth + 1,
                    total_tokens=budget_status.tokens_used,
                    total_cost_usd=budget_status.cost_used,
                    wall_time_seconds=time.time() - start_time,
                    trajectory=trajectory,
                    error="Max recursion depth reached",
                    error_type="budget_exceeded",
                )

            # Use provided context or default to empty string
            sub_ctx: ContextType = context if context is not None else ""

            resp = await self._run(
                query=query,
                context=sub_ctx,
                depth=depth + 1,
                root_model=root_model,
                sub_model=sub_model,
                budget=budget,
                budget_status=budget_status,
                start_time=start_time,
                trajectory=trajectory,
                temperature=temperature,
                next_step_number=next_step_number,
            )

            return resp

        return sub_aleph

    # ---------------------------------------------------------------------
    # Prompting / parsing
    # ---------------------------------------------------------------------

    def _build_initial_messages(self, query: str, meta: ContextMetadata) -> list[Message]:
        system = self.system_prompt.format(
            query=query,
            context_var=self.context_var_name,
            context_format=meta.format.value,
            context_size_chars=meta.size_chars,
            context_size_lines=meta.size_lines,
            context_size_tokens=meta.size_tokens_estimate,
            context_preview=meta.sample_preview,
            structure_hint=meta.structure_hint or "N/A",
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ]

    def _parse_response(self, text: str) -> ParsedAction:
        if _FINAL_VAR_RE.search(text):
            return ParsedAction(ActionType.FINAL_VAR, "", text)
        if _FINAL_RE.search(text):
            return ParsedAction(ActionType.FINAL_ANSWER, "", text)

        m = _CODE_BLOCK_RE.search(text)
        if m:
            return ParsedAction(ActionType.CODE_BLOCK, m.group(1).strip(), text)

        return ParsedAction(ActionType.CONTINUE, "", text)

    def _extract_final(self, text: str) -> str:
        m = _FINAL_RE.search(text)
        if not m:
            return text.strip()
        return m.group(1).strip()

    def _extract_final_var(self, text: str) -> str:
        m = _FINAL_VAR_RE.search(text)
        if not m:
            return ""
        raw = m.group(1).strip()
        # Allow FINAL_VAR("name") or FINAL_VAR('name')
        if len(raw) >= 2 and ((raw[0] == raw[-1] == '"') or (raw[0] == raw[-1] == "'")):
            raw = raw[1:-1].strip()
        return raw

    def _format_repl_result(self, result: ExecutionResult) -> str:
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append("[STDERR]\n" + result.stderr)
        if result.error and (not result.stderr):
            parts.append("[ERROR]\n" + result.error)
        if result.return_value is not None:
            parts.append(f"[RETURN_VALUE]\n{result.return_value}")

        out = "\n".join(parts).strip()
        if not out:
            out = "(no output)"
        return f"```output\n{out}\n```"

    def _trim_messages(self, messages: list[Message], model: str) -> None:
        """Best-effort trimming to avoid blowing past the context limit."""

        context_limit = self.provider.get_context_limit(model)
        # Reserve room for the model's output.
        reserve = min(self.provider.get_output_limit(model), 8192)
        target = max(1_000, context_limit - reserve)

        # Rough token count
        def msg_tokens(ms: list[Message]) -> int:
            return sum(self.provider.count_tokens(m.get("content", ""), model) for m in ms)

        if msg_tokens(messages) <= target:
            return

        # Always keep the system message. Keep the last few turns.
        system = messages[0:1]
        tail = messages[-8:]
        pruned = system + tail
        # If still too large, progressively drop older tail messages (but keep user query at least)
        while len(pruned) > 2 and msg_tokens(pruned) > target:
            pruned = system + pruned[2:]

        messages.clear()
        messages.extend(pruned)

    # ---------------------------------------------------------------------
    # Context analysis
    # ---------------------------------------------------------------------

    def _analyze_context(self, context: ContextType) -> ContextMetadata:
        """Compute lightweight metadata for the root prompt."""

        # Multi-doc collection
        if isinstance(context, ContextCollection):
            total_bytes = 0
            total_chars = 0
            total_lines = 0
            preview = ""
            structure = f"ContextCollection with {len(context.items)} items"

            for i, (name, item) in enumerate(context.items):
                item_meta = self._analyze_context(item)
                total_bytes += item_meta.size_bytes
                total_chars += item_meta.size_chars
                total_lines += item_meta.size_lines
                if i == 0:
                    preview = f"[{name}]\n" + item_meta.sample_preview

            est_tokens = total_chars // 4 if total_chars else 0
            context.total_size_bytes = total_bytes
            context.total_size_tokens_estimate = est_tokens

            return ContextMetadata(
                format=ContentFormat.MIXED,
                size_bytes=total_bytes,
                size_chars=total_chars,
                size_lines=total_lines,
                size_tokens_estimate=est_tokens,
                structure_hint=structure,
                sample_preview=preview[:500],
            )

        # Plain text
        if isinstance(context, str):
            return ContextMetadata(
                format=ContentFormat.TEXT,
                size_bytes=len(context.encode("utf-8", errors="ignore")),
                size_chars=len(context),
                size_lines=context.count("\n") + 1,
                size_tokens_estimate=max(1, len(context) // 4) if context else 0,
                structure_hint=None,
                sample_preview=context[:500],
            )

        # Bytes
        if isinstance(context, (bytes, bytearray)):
            b = bytes(context)
            preview = b[:200].decode("utf-8", errors="replace")
            return ContextMetadata(
                format=ContentFormat.BINARY,
                size_bytes=len(b),
                size_chars=len(preview),
                size_lines=preview.count("\n") + 1,
                size_tokens_estimate=max(1, len(preview) // 4) if preview else 0,
                structure_hint="binary payload (preview decoded as utf-8)",
                sample_preview=preview[:500],
            )

        # JSON-like
        if isinstance(context, dict):
            text = json.dumps(context, indent=2, ensure_ascii=False)
            keys = list(context.keys())
            hint = f"JSON object with keys: {keys[:10]}" if keys else "JSON object"
            return ContextMetadata(
                format=ContentFormat.JSON,
                size_bytes=len(text.encode("utf-8", errors="ignore")),
                size_chars=len(text),
                size_lines=text.count("\n") + 1,
                size_tokens_estimate=max(1, len(text) // 4) if text else 0,
                structure_hint=hint,
                sample_preview=text[:500],
            )

        if isinstance(context, list):
            text = json.dumps(context[:100], indent=2, ensure_ascii=False)
            hint = f"JSON array (showing first {min(len(context), 100)} of {len(context)})"
            return ContextMetadata(
                format=ContentFormat.JSON,
                size_bytes=len(text.encode("utf-8", errors="ignore")),
                size_chars=len(text),
                size_lines=text.count("\n") + 1,
                size_tokens_estimate=max(1, len(text) // 4) if text else 0,
                structure_hint=hint,
                sample_preview=text[:500],
            )

        # Fallback
        text = str(context)
        return ContextMetadata(
            format=ContentFormat.TEXT,
            size_bytes=len(text.encode("utf-8", errors="ignore")),
            size_chars=len(text),
            size_lines=text.count("\n") + 1,
            size_tokens_estimate=max(1, len(text) // 4) if text else 0,
            structure_hint=f"Python object: {type(context).__name__}",
            sample_preview=text[:500],
        )

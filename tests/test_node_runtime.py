from __future__ import annotations

import shutil
from typing import Any

import pytest

from aleph.repl.node_runtime import NodeREPLEnvironment


NODE_AVAILABLE = shutil.which("node") is not None


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required for JS/TS REPL tests")
class TestNodeRuntime:
    def test_exec_javascript_expression(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="hello", config=sandbox_config)
        try:
            result = repl.execute("1 + 1")
            assert result.error is None
            assert result.return_value == 2
        finally:
            repl.close()

    def test_exec_typescript_expression(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="hello", config=sandbox_config)
        try:
            result = repl.execute("const answer: number = 40; answer + 2", language="typescript")
            assert result.error is None
            assert result.return_value == 42
        finally:
            repl.close()

    def test_exec_typescript_expression_with_fallback_strip(self, sandbox_config, monkeypatch) -> None:
        monkeypatch.setenv("ALEPH_NODE_FORCE_TS_FALLBACK", "true")
        repl = NodeREPLEnvironment(context="hello", config=sandbox_config)
        try:
            result = repl.execute(
                """
const routes: string[] = ["read", "write"];
const mapped = routes.map((route: string) => route.toUpperCase());
const report: { routeCount: number; first: string } = {
  routeCount: mapped.length,
  first: mapped[0],
};
report
                """,
                language="typescript",
            )
            assert result.error is None
            assert result.return_value == {"routeCount": 2, "first": "READ"}
        finally:
            repl.close()

    def test_context_helpers_and_variable_lookup(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="Line 1: Hello World\nLine 2: Goodbye", config=sandbox_config)
        try:
            repl.set_variable("line_number_base", 1)
            result = repl.execute("const hits = search('Hello'); const answer = hits[0].match; answer")
            assert result.error is None
            assert result.return_value == "Line 1: Hello World"
            assert "answer" in result.variables_updated
            assert repl.get_variable("answer") == "Line 1: Hello World"
        finally:
            repl.close()

    def test_ctx_mutation_persists(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="before", config=sandbox_config)
        try:
            repl.execute("ctx_set('after')")
            assert repl.get_variable("ctx") == "after"
            repl.execute("ctx_append(' plus')")
            assert repl.get_variable("ctx") == "after plus"
        finally:
            repl.close()

    def test_top_level_await_callback_persists_variables(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="alpha\nbeta", config=sandbox_config)
        repl.register_callback(
            "sub_query",
            lambda prompt, context_slice=None: f"{prompt}|{context_slice}",
        )
        try:
            result = repl.execute(
                "const answer = await sub_query('Summarize', lines(0, 1)); answer",
            )
            assert result.error is None
            assert result.return_value == "Summarize|alpha"
            assert repl.get_variable("answer") == "Summarize|alpha"
        finally:
            repl.close()

    def test_expanded_text_helpers(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(
            context="TODO: investigate\nemail me at dev@example.com\n\nline 3",
            config=sandbox_config,
        )
        try:
            result = repl.execute(
                "({ emails: extract_emails(), todos: extract_todos(), numbered: number_lines(), paragraphs: paragraph_count() })",
            )
            assert result.error is None
            payload = result.return_value
            assert isinstance(payload, dict)
            assert payload["emails"][0]["value"] == "dev@example.com"
            assert payload["todos"][0]["value"] == "TODO: investigate"
            assert "1: TODO: investigate" in payload["numbered"]
            assert payload["paragraphs"] == 2
        finally:
            repl.close()

    def test_standalone_helper_parity(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="alpha", config=sandbox_config)
        try:
            result = repl.execute(
                """
const csv = to_csv_row(['a,b', 'c"d']);
({
  diff: diff('alpha\\nbeta', 'alpha\\nbravo'),
  similarity: similarity('kitten', 'sitting'),
  common: common_lines('a\\nb\\nc', 'b\\nc\\nd'),
  diffLines: diff_lines('a\\nb', 'b\\nc'),
  embed: embed_text('alpha beta', 8),
  flat: flatten([1, [2, [3, 4]]]),
  first: first([], 'none'),
  last: last([1, 2, 3]),
  taken: take(2, [1, 2, 3]),
  dropped: drop(1, [1, 2, 3]),
  partitioned: partition([1, 2, 3, 4], (value) => value % 2 === 0),
  grouped: group_by(
    [{ ext: 'ts', name: 'a.ts' }, { ext: 'py', name: 'b.py' }, { ext: 'ts', name: 'c.ts' }],
    (item) => item.ext,
  ),
  frequency: frequency(['ts', 'py', 'ts'], 2),
  sample: sample_items(['a', 'b', 'c', 'd'], 2, 7),
  shuffled: shuffle_items(['a', 'b', 'c', 'd'], 7),
  isNumeric: is_numeric('1,234.5'),
  isEmail: is_email('dev@example.com'),
  isUrl: is_url('https://example.com/docs'),
  isIp: is_ip('127.0.0.1'),
  isUuid: is_uuid('123e4567-e89b-12d3-a456-426614174000'),
  isJson: is_json('{"ok": true}'),
  isBlank: is_blank('   '),
  csv,
  parsedCsv: from_csv_row(csv),
})
                """
            )
            assert result.error is None
            payload = result.return_value
            assert isinstance(payload, dict)
            assert "-beta" in payload["diff"]
            assert "+bravo" in payload["diff"]
            assert 0 < payload["similarity"] < 1
            assert payload["common"] == ["b", "c"]
            assert payload["diffLines"] == {"only_in_first": ["a"], "only_in_second": ["c"]}
            assert len(payload["embed"]) == 8
            assert payload["flat"] == [1, 2, 3, 4]
            assert payload["first"] == "none"
            assert payload["last"] == 3
            assert payload["taken"] == [1, 2]
            assert payload["dropped"] == [2, 3]
            assert payload["partitioned"] == [[2, 4], [1, 3]]
            assert payload["grouped"]["ts"] == [
                {"ext": "ts", "name": "a.ts"},
                {"ext": "ts", "name": "c.ts"},
            ]
            assert payload["frequency"][0] == ["ts", 2]
            assert payload["sample"] == payload["shuffled"][:2]
            assert payload["isNumeric"] is True
            assert payload["isEmail"] is True
            assert payload["isUrl"] is True
            assert payload["isIp"] is True
            assert payload["isUuid"] is True
            assert payload["isJson"] is True
            assert payload["isBlank"] is True
            assert payload["parsedCsv"] == ["a,b", 'c"d']
        finally:
            repl.close()

    def test_top_level_await_multistep_composition_updates_ctx(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="alpha\nbeta", config=sandbox_config)
        try:
            result = repl.execute(
                """
const pieces = await Promise.all([
  Promise.resolve(lines(0, 1)),
  Promise.resolve(lines(1, 2)),
]);
const merged = pieces.filter(Boolean).join('|');
ctx_set(merged);
({ merged, ctx })
                """
            )
            assert result.error is None
            assert result.return_value == {"merged": "alpha|beta", "ctx": "alpha|beta"}
            assert repl.get_variable("ctx") == "alpha|beta"
        finally:
            repl.close()

    def test_callback_error_surfaces_in_execution_result(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="alpha", config=sandbox_config)

        def _boom(*_args: Any, **_kwargs: Any) -> str:
            raise RuntimeError("bridge exploded")

        repl.register_callback("sub_query", _boom)
        try:
            result = repl.execute("await sub_query('Summarize', ctx)")
            assert result.error is not None
            assert "bridge exploded" in result.error
        finally:
            repl.close()

    def test_worker_restart_rehydrates_context_and_callbacks(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="alpha\nbeta", config=sandbox_config)
        repl.register_callback(
            "sub_query",
            lambda prompt, context_slice=None: f"{prompt}|{context_slice}",
        )
        try:
            repl.set_variable("line_number_base", 7)
            first = repl.execute("ctx_set('restart-ready\\nsecond')")
            assert first.error is None
            process = repl._process
            assert process is not None
            process.kill()
            process.wait(timeout=2)

            result = repl.execute(
                "const answer = await sub_query('After restart', lines(0, 1)); ({ answer, ctx, lineBase: line_number_base })",
            )
            assert result.error is None
            assert result.return_value == {
                "answer": "After restart|restart-ready",
                "ctx": "restart-ready\nsecond",
                "lineBase": 7,
            }
        finally:
            repl.close()


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required for JS/TS REPL tests")
class TestNodeRecipeDSL:
    """Test Recipe DSL helpers exposed in the Node sandbox."""

    def test_recipe_step_to_dict(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'const s = new RecipeStep("search", { pattern: "error" }); s.toDict()'
            )
            assert result.error is None
            assert result.return_value == {"op": "search", "pattern": "error"}
        finally:
            repl.close()

    def test_recipe_builder_fluent_chain(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'Recipe("myctx").search("error").take(5).finalize().compile()'
            )
            assert result.error is None
            recipe = result.return_value
            assert recipe["version"] == "aleph.recipe.v1"
            assert recipe["context_id"] == "myctx"
            assert len(recipe["steps"]) == 3
            assert recipe["steps"][0]["op"] == "search"
            assert recipe["steps"][0]["pattern"] == "error"
            assert recipe["steps"][1]["op"] == "take"
            assert recipe["steps"][1]["count"] == 5
            assert recipe["steps"][2]["op"] == "finalize"
        finally:
            repl.close()

    def test_recipe_builder_pipe_method(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'Recipe().pipe(Search("warn")).pipe(Take(3)).compile()'
            )
            assert result.error is None
            recipe = result.return_value
            assert len(recipe["steps"]) == 2
            assert recipe["steps"][0]["pattern"] == "warn"
            assert recipe["steps"][1]["count"] == 3
        finally:
            repl.close()

    def test_recipe_builder_immutability(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute("""
                const base = Recipe("ctx1");
                const a = base.search("alpha");
                const b = base.search("beta");
                ({ aSteps: a.steps.length, bSteps: b.steps.length, baseSteps: base.steps.length })
            """)
            assert result.error is None
            assert result.return_value == {"aSteps": 1, "bSteps": 1, "baseSteps": 0}
        finally:
            repl.close()

    def test_recipe_builder_with_budget(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'Recipe().withBudget({ maxSteps: 10, maxSubQueries: 3 }).search("x").compile()'
            )
            assert result.error is None
            recipe = result.return_value
            assert recipe["budget"]["max_steps"] == 10
            assert recipe["budget"]["max_sub_queries"] == 3
        finally:
            repl.close()

    def test_all_step_constructors(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute("""
                Recipe("doc")
                  .pipe(Search("err", { maxResults: 10 }))
                  .pipe(Peek({ start: 0, end: 100 }))
                  .pipe(Lines({ start: 5, end: 20 }))
                  .pipe(Take(3))
                  .pipe(Chunk(500, { overlap: 50 }))
                  .pipe(Filter({ contains: "ERROR" }))
                  .pipe(MapSubQuery("Summarize this", { backend: "codex", limit: 5 }))
                  .pipe(SubQuery("What is the root cause?"))
                  .pipe(Aggregate("Combine findings"))
                  .pipe(Assign("results"))
                  .pipe(Load("results"))
                  .pipe(Finalize())
                  .compile()
            """)
            assert result.error is None
            recipe = result.return_value
            ops = [s["op"] for s in recipe["steps"]]
            assert ops == [
                "search", "peek", "lines", "take", "chunk", "filter",
                "map_sub_query", "sub_query", "aggregate", "assign", "load", "finalize",
            ]
            assert recipe["steps"][0]["max_results"] == 10
            assert recipe["steps"][4]["chunk_size"] == 500
            assert recipe["steps"][4]["overlap"] == 50
            assert recipe["steps"][6]["limit"] == 5
            assert recipe["steps"][6]["backend"] == "codex"
        finally:
            repl.close()

    def test_as_recipe_from_builder(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'as_recipe(Recipe("doc").search("x").finalize())'
            )
            assert result.error is None
            assert result.return_value["context_id"] == "doc"
            assert len(result.return_value["steps"]) == 2
        finally:
            repl.close()

    def test_as_recipe_from_plain_object(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'as_recipe({ version: "aleph.recipe.v1", context_id: "x", steps: [{ op: "search", pattern: "y" }] })'
            )
            assert result.error is None
            assert result.return_value["context_id"] == "x"
        finally:
            repl.close()

    def test_recipe_null_params_omitted(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'Search("test").toDict()'
            )
            assert result.error is None
            payload = result.return_value
            assert "input" not in payload
            assert "store" not in payload
        finally:
            repl.close()

    def test_recipe_typescript_compilation(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'const r: object = Recipe("ts").search("err").take(2).compile(); r',
                language="typescript",
            )
            assert result.error is None
            assert result.return_value["steps"][0]["op"] == "search"
            assert result.return_value["steps"][1]["count"] == 2
        finally:
            repl.close()

    def test_recipe_typescript_compilation_with_fallback_strip(self, sandbox_config, monkeypatch) -> None:
        monkeypatch.setenv("ALEPH_NODE_FORCE_TS_FALLBACK", "true")
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'const r: object = Recipe("ts").search("err").take(2).compile(); r',
                language="typescript",
            )
            assert result.error is None
            assert result.return_value["steps"][0]["op"] == "search"
            assert result.return_value["steps"][1]["count"] == 2
        finally:
            repl.close()

    def test_recipe_json_serialization(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute(
                'JSON.stringify(Recipe("doc").search("x").compile())'
            )
            assert result.error is None
            import json
            parsed = json.loads(result.return_value)
            assert parsed["version"] == "aleph.recipe.v1"
            assert parsed["steps"][0]["op"] == "search"
        finally:
            repl.close()

    def test_recipe_version_constant(self, sandbox_config) -> None:
        repl = NodeREPLEnvironment(context="test", config=sandbox_config)
        try:
            result = repl.execute("RECIPE_DSL_VERSION")
            assert result.error is None
            assert result.return_value == "aleph.recipe.v1"
        finally:
            repl.close()

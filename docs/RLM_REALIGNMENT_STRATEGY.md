# RLM Realignment Strategy

Date: 2026-02-05
Owner: Aleph maintainers
Status: Proposed execution plan

## 1) Goal

Realign Aleph with Recursive Language Model (RLM) research on two axes:

1. Presentation: clearly distinguish canonical RLM behavior from Aleph-specific platform features.
2. Ability: strengthen measurable RLM behavior (decompose, recurse, aggregate, verify) with explicit evaluation and regression checks.

## 2) Research Baseline (What "RLM-aligned" Means)

Using the RLM paper and official implementation as source of truth, canonical RLM behavior is:

1. Treat prompt/context as an external environment object, not as raw prompt tokens.
2. Use a REPL-style symbolic loop: inspect, decompose, execute, observe.
3. Recursively call sub-LMs for decomposition/aggregation when useful.
4. Iterate until a final answer is produced.

Important nuance from research:

1. RLM is an inference framework, not a product surface by itself.
2. RLM can outperform long-context baselines on information-dense tasks, but performance is workload-dependent.
3. Paper limitations explicitly call out open areas: optimal execution strategy, async sub-calls, and recursion-depth policy.

## 3) Current Repo Audit

### 3.1 What is already strongly aligned

1. Core closed-loop RLM runtime exists:
   - `aleph/core.py` implements iterative root loop + REPL + `FINAL(...)` protocol.
   - `aleph/core.py` supports `sub_query` and recursive `sub_aleph`.
2. Externalized context + symbolic interaction exists:
   - Context is loaded into `ctx` and explored via helpers/search/code execution.
3. Recursion budget controls exist:
   - Depth, iterations, wall time, and sub-query budgets are enforced.
4. Recursion behavior has test coverage:
   - Nested recursion and sub-query backends are tested in `tests/test_double_recursion.py` and `tests/test_sub_query.py`.

### 3.2 Where presentation currently drifts

1. Mode boundary is under-explicit:
   - Docs position Aleph broadly as RLM-based, but do not consistently separate:
     - canonical "Aleph core loop mode" (`aleph run`)
     - MCP tool-server orchestration mode (RLM-inspired, host-model-driven).
2. Documentation inconsistency reduces credibility:
   - `DEVELOPMENT.md` has stale backend priority and stale budget schema examples.
   - `docs/CONFIGURATION.md` timeout defaults are outdated versus runtime defaults.
3. Some guidance is phrased as universal when it is backend-dependent:
   - Prompt/docs imply fixed sub-query capacity heuristics while runtime enforces configurable truncation limits.

### 3.3 Where ability currently drifts from research rigor

1. No first-class benchmark harness reproducing paper-style task families in CI.
2. No published "research profile" config that reproduces paper-like control settings for fair comparison.
3. No recurring regression report tracking RLM-specific quality dimensions over time (decomposition quality, recursion efficiency, cost variance).

## 4) Strategy

Run two workstreams in parallel, then unify with an evaluation gate.

## Workstream A: Presentation Realignment

### A1. Introduce explicit product taxonomy

Add one canonical section to top-level docs:

1. Core RLM Mode (paper-aligned loop): `aleph run`, `alef`, `Aleph.complete(...)`.
2. RLM Infrastructure Mode (MCP external memory server): tool-driven orchestration from host assistants.
3. Platform Extensions (not in canonical RLM): swarm workflows, remote MCP orchestration, workspace/action tooling.

### A2. Establish a "Claims Ledger"

Create `docs/RLM_CLAIMS_LEDGER.md` mapping every major claim to one of:

1. Research-backed (with citation)
2. Code-backed (with repo file reference)
3. Aspirational (roadmap only)

No claim should remain unclassified.

### A3. Remove doc drift and ambiguity

Synchronize:

1. `README.md`
2. `docs/CONFIGURATION.md`
3. `DEVELOPMENT.md`
4. `docs/prompts/aleph.md`

Rules:

1. Defaults must match runtime constants.
2. Backend priority must match `aleph/sub_query/__init__.py`.
3. Capacity guidance must mention truncation/config dependence.
4. "RLM" label should be used precisely: canonical loop vs inspired tooling.

## Workstream B: Ability Realignment

### B1. Add an RLM benchmark harness

Create a lightweight benchmark module with deterministic synthetic tasks mirroring paper structure:

1. Constant-information retrieval task (S-NIAH-style)
2. Linear aggregation task (OOLONG-style)
3. Pairwise aggregation task (OOLONG-Pairs-style)
4. Code context reasoning task (CodeQA-style)

Output JSON report fields:

1. accuracy
2. total tokens
3. wall time
4. recursion depth used
5. sub-query count
6. cost estimate (if provider supports it)

### B2. Add "research profile" runtime preset

Add a named config profile (example: `--profile rlm-paper-like`) that pins:

1. recursion depth
2. iteration cap
3. sub-query cap
4. prompt template variant
5. model split policy (root vs sub-model)

Purpose: make "paper-like mode" reproducible and auditable.

### B3. Add regression gates for RLM behavior

Extend tests with behavior-level checks:

1. decomposition quality (does model/toolchain split input and aggregate correctly on fixed fixtures)
2. recursion efficiency (budget use stays within expected bounds for fixtures)
3. stability (result variance under repeated runs with controlled temperature)

## Workstream C: Evidence and Reporting

### C1. Publish periodic evaluation snapshots

Add `docs/reports/rlm-eval-YYYY-MM-DD.md` containing:

1. benchmark outcomes
2. deltas versus previous run
3. regression explanations
4. confidence and known blind spots

### C2. Tie docs to evidence

Any top-level performance statement in README must point to:

1. paper citation, or
2. local benchmark report,

and include run date.

## 5) Execution Plan (Phased)

## Phase 0 (1-2 days): Correctness of messaging

1. Publish taxonomy section in `README.md`.
2. Fix stale defaults/priority mismatches in `DEVELOPMENT.md` and `docs/CONFIGURATION.md`.
3. Add `docs/RLM_CLAIMS_LEDGER.md` scaffold.

Exit criteria:

1. No contradictory defaults between docs and runtime for sub-query/backend/timeouts.
2. Every major README claim tagged as research-backed, code-backed, or aspirational.

## Phase 1 (3-5 days): Measurement baseline

1. Implement benchmark harness and fixture set.
2. Add CI job that runs a reduced benchmark subset nightly.
3. Commit first `docs/reports/rlm-eval-*.md`.

Exit criteria:

1. Reproducible benchmark report exists.
2. README claims reference concrete report or paper.

## Phase 2 (1-2 weeks): Capability hardening

1. Add research profile preset.
2. Add regression tests for decomposition/aggregation and recursion-budget behavior.
3. Improve sub-query policy controls (parallelism policy, truncation observability, retry behavior) behind explicit config.

Exit criteria:

1. RLM-specific regression gates exist in CI.
2. Profile-based reproducibility for paper-like runs is documented.

## 6) Success Metrics

Presentation metrics:

1. Zero stale-default mismatches between docs and runtime constants.
2. 100% of top-level claims linked to paper or local reports.
3. Clear user understanding of mode boundaries in docs review (qualitative check).

Ability metrics:

1. Benchmark suite committed and reproducible.
2. Stable pass/fail thresholds for synthetic RLM task families.
3. Trendline report shows non-regressing decomposition/aggregation quality.

## 7) Risks and Mitigations

1. Risk: Overfitting to synthetic benchmarks.
   - Mitigation: include both synthetic structure tests and real-world corpus fixtures.
2. Risk: Benchmark cost/runtime explosion.
   - Mitigation: tiered benchmark modes (smoke/nightly/full).
3. Risk: Docs become stale again.
   - Mitigation: add CI check that validates documented defaults against code constants.

## 8) Immediate Next Actions

1. Approve this plan as the execution baseline.
2. Start Phase 0 with doc corrections and claims ledger.
3. Open tracking issues for Phase 1 benchmark harness and Phase 2 regression gates.

## 9) Sources

1. Recursive Language Models paper (arXiv): https://arxiv.org/abs/2512.24601
2. Official RLM codebase: https://github.com/alexzhang13/rlm
3. Author article on RLM framing and limitations:
   https://towardsdatascience.com/recursive-language-models-new-rules-for-agentic-ai/
